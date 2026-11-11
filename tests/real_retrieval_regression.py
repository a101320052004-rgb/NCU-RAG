"""Fixed-snapshot RAG comparison with real existing E5 and PostgreSQL/pgvector.

No production connection, Claude, new answers or edits to original test results.
The snapshot's redacted text is re-embedded; this is a new controlled replay,
not a reconstruction of historical vectors or production performance.
"""
from pathlib import Path
import contextlib
import csv
import hashlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock, patch

import argparse
import importlib.metadata
import posixpath
import shutil
import zipfile
from xml.etree import ElementTree as ET

parser=argparse.ArgumentParser()
parser.add_argument('--package',type=Path,required=True,help='Private original diagnostic package directory')
parser.add_argument('--workbook',type=Path,required=True,help='Original evaluation XLSX; opened read-only')
parser.add_argument('--cache-dir',type=Path,required=True,help='Private model/vector cache OUTSIDE the repository')
parser.add_argument('--pydeps',type=Path,help='Optional isolated Python dependency directory')
args=parser.parse_args()
REPO=Path(__file__).resolve().parents[1]
ROOT=args.cache_dir.resolve()
PACKAGE=args.package.resolve()
if ROOT==REPO or REPO in ROOT.parents:
    raise ValueError('Cache directory must be outside the repository')
ROOT.mkdir(parents=True,exist_ok=True)
if args.pydeps:sys.path.insert(0,str(args.pydeps.resolve()))
sys.path.insert(0,str(REPO/'tests'))
try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

def workbook_questions(file):
    ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(file) as z:
        strings=[''.join(t.text or '' for t in si.iterfind('.//s:t',ns)) for si in ET.fromstring(z.read('xl/sharedStrings.xml'))] if 'xl/sharedStrings.xml' in z.namelist() else []
        wb=ET.fromstring(z.read('xl/workbook.xml'))
        rels={r.attrib['Id']:r.attrib['Target'] for r in ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))}
        sheet=next(s for s in wb.find('s:sheets',ns) if s.attrib['name']=='60題測試題庫')
        target=rels[sheet.attrib['{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id']]
        target=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
        out=[]
        for row in ET.fromstring(z.read(target)).findall('s:sheetData/s:row',ns):
            if not 5<=int(row.attrib['r'])<=64:continue
            values={}
            for cell in row.findall('s:c',ns):
                col=''.join(c for c in cell.attrib['r'] if c.isalpha())
                if col not in ['A','E','G','J']:continue
                value=cell.find('s:v',ns)
                if cell.attrib.get('t')=='s':text=strings[int(value.text)] if value is not None else ''
                elif cell.attrib.get('t')=='inlineStr':text=''.join(t.text or '' for t in cell.findall('.//s:t',ns))
                else:text=value.text if value is not None else ''
                values[col]=text
            out.append(values)
        assert len(out)==60
        return out

workbook_hash=hashlib.sha256(args.workbook.read_bytes()).hexdigest()
import numpy as np
import test_reliability as base

cfg=base.config()
cfg.EMBEDDER='e5_onnx';cfg.MODEL_DIR=ROOT/'models'
cfg.E5_ONNX_REPO='Xenova/multilingual-e5-small';cfg.E5_ONNX_FILE='onnx/model_quantized.onnx'
model=base.load('actual_embedding',REPO/'05_程式碼/app/embedder.py',{'config':cfg})
model.warm_up()
model_folder=cfg.MODEL_DIR/cfg.E5_ONNX_REPO.replace('/','__')
model_files={n:hashlib.sha256((model_folder/n).read_bytes()).hexdigest() for n in ['tokenizer.json',cfg.E5_ONNX_FILE]}
versions={p:importlib.metadata.version(p) for p in ['onnxruntime','tokenizers','numpy']}
manifest_path=ROOT/'model_manifest.json'
if manifest_path.exists():
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['file_hashes']==model_files,'Model files changed; use a separate cache'
    assert all(manifest[p]==v for p,v in versions.items()),'Runtime changed; use a separate cache'
else:
    manifest={'model':cfg.E5_ONNX_REPO,'file_hashes':model_files,**versions,'revision_note':'Exact model file hashes recorded; no historical revision assumed.'}
    manifest_path.write_text(json.dumps(manifest,indent=2),encoding='utf-8')
kb=[json.loads(s) for s in (PACKAGE/'database/knowledge_chunks_fulltext.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
metadata={r['chunk_id']:r for r in csv.DictReader((PACKAGE/'database/knowledge_metadata.csv').open(encoding='utf-8-sig'))}
by_id={c['id']:c for c in kb}
texts=[c['text'] for c in kb]
cache_key=hashlib.sha256(json.dumps([manifest['file_hashes'],[(c['id'],hashlib.sha256(c['text'].encode()).hexdigest()) for c in kb]],sort_keys=True).encode()).hexdigest()
cache=ROOT/(cache_key+'.npz')
if cache.exists():
    vectors=np.load(cache)['vectors']
else:
    vectors=[]
    started=time.monotonic()
    for i in range(0,len(texts),16):
        vectors.extend(model.embed_passages(texts[i:i+16]))
        if i%160==0:print(f'Embedded {min(i+16,len(texts))}/{len(texts)} passages, {time.monotonic()-started:.0f}s',flush=True)
    vectors=np.asarray(vectors,dtype=np.float32)
    np.savez_compressed(cache,vectors=vectors)
assert vectors.shape==(1195,384)

qcache_path=ROOT/'query_cache.json'
qcache=json.loads(qcache_path.read_text(encoding='utf-8')) if qcache_path.exists() else {}
qcache=qcache if qcache.get('model_files')==manifest['file_hashes'] else {'model_files':manifest['file_hashes'],'vectors':{}}
def query_vector(q):
    if q not in qcache['vectors']:
        qcache['vectors'][q]=model.embed_query(q)
    return qcache['vectors'][q]
embedding=SimpleNamespace(embed_query=query_vector)

engine=subprocess.Popen([shutil.which('node') or 'node',str(REPO/'tests/sql_engine/engine.mjs'),str(REPO),str(PACKAGE),'bridge'],
                        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=sys.stderr,text=True,encoding='utf-8',bufsize=1)
ready_line=engine.stdout.readline()
if not ready_line:raise RuntimeError('SQL engine did not start; run npm ci --prefix tests/sql_engine first')
ready=json.loads(ready_line);assert ready['ready'],ready
def send(item):
    engine.stdin.write(json.dumps(item,ensure_ascii=False)+'\n');engine.stdin.flush()
    line=engine.stdout.readline()
    if not line:raise RuntimeError('Memory SQL engine ended')
    result=json.loads(line)
    if not result.get('ok'):raise RuntimeError(result.get('error'))
    return result['value']
vec=lambda v:'['+','.join(f'{x:.6f}' for x in v)+']'
for i in range(0,len(kb),100):
    rows=[]
    for c,v in zip(kb[i:i+100],vectors[i:i+100]):
        m=metadata[c['id']]
        rows.append({**{k:value for k,value in c.items() if k!='text'},'body':c['text'],
                     'identities':json.loads(m['identities']),'topics':json.loads(m['topics']),'embedding':vec(v)})
    send({'op':'seed','rows':rows})
sb=SimpleNamespace(vec=vec,rpc=lambda name,args:send({'op':'rpc','name':name,'args':args}))
modules={'config':cfg,'db':Mock(),'contacts':Mock(),'embedder':embedding}
before=base.load('baseline_rag',PACKAGE/'source/deployed_app/rag.py',modules)
after=base.load('repaired_rag',REPO/'05_程式碼/app/rag.py',modules)
golden=['8af182dee53384c3-'+str(n) for n in [32,33,34]]
def clean(t):
    import re
    return re.sub(r'\s+',' ',re.sub(r'^【[^】]*】\n?','',t)).strip()
def metric(context):
    joined=clean('\n'.join(h['text'] for h in context))
    return sum(clean(by_id[c]['text']) in joined for c in golden)/3
def run(q,identity):
    start=time.monotonic()
    with patch.dict(sys.modules,{'sb':sb}):
        old=before.retrieve(q,identity,before.detect_lang(q));oldctx=before.expand_context(old)
        new=after.retrieve(q,identity,after.detect_lang(q));newctx=after.expand_context(new,identity)
    # These are retrieval/coverage measures, never a pass/fail grade for answers.
    return {'before_ids':[h['meta']['id'] for h in old], 'after_ids':[h['meta']['id'] for h in new],
            'before_context_sha256':[hashlib.sha256(h['text'].encode()).hexdigest() for h in oldctx],
            'after_context_sha256':[hashlib.sha256(h['text'].encode()).hexdigest() for h in newctx],
            'before_fee_table_recall':metric(oldctx),'after_fee_table_recall':metric(newctx),
            'before_work_relevant_sources':sum(bool(re.search(r'work permit|工作證|工作許可',(h['meta'].get('title') or '')+' '+h['text'],re.I)) for h in old),
            'after_work_relevant_sources':sum(bool(re.search(r'work permit|工作證|工作許可',(h['meta'].get('title') or '')+' '+h['text'],re.I)) for h in new),
            'before_official_work_page':any(h['meta']['id'].startswith('a20d7a7e9a628894-') for h in old),
            'after_official_work_page':any(h['meta']['id'].startswith('a20d7a7e9a628894-') for h in new),
            'before_work_source_rank':next((i for i,h in enumerate(old,1) if h['meta']['id'].startswith('a20d7a7e9a628894-')),None),
            'after_work_source_rank':next((i for i,h in enumerate(new,1) if h['meta']['id'].startswith('a20d7a7e9a628894-')),None),
            'after_context_has_legacy_arc_fragment':any(clean(by_id['084a1a606d7ea9d4-2']['text']) in clean(h['text']) for h in newctx),
            'after_context_has_known_obsolete_claim':any(base.policy.policy_claim(h['text'])[1] for h in newctx),
            'elapsed_seconds':round(time.monotonic()-start,3)}

try:
    questions=workbook_questions(args.workbook)
    workbook=[]
    for q in questions:
        if q['J']!='已測' or q['A'].startswith('H'):continue
        result=run(q['G'],q['E'] if q['E'] in base.IDENTITIES else None)
        workbook.append({'id':q['A'],'identity':q['E'],**result})
        print(f"Compared {q['A']} ({len(workbook)}/47)",flush=True)
    fees=[]
    for identity in [None]+base.IDENTITIES:
        for label,q in [('fees','What are the dormitory fees?'),('cost','How much does the dorm cost?'),('charges','What are the housing charges?'),('rates','What are the accommodation rates?'),('Chinese','宿舍費用是多少？')]:
            fees.append({'variant':label,'identity':identity,**run(q,identity)})
        print('Fee identity group complete: '+str(identity),flush=True)
    work=[]
    for identity in [None]+base.IDENTITIES:
        for term in ['work permit','permission to work','employment authorization','authorisation to work']:
            work.append({'variant':term,'identity':identity,**run(f'How can I apply for {term} while studying at NCU?',identity)})
        print('Work identity group complete: '+str(identity),flush=True)
    calibration=[]
    for n in range(1,9):
        recorded=json.loads((PACKAGE/f'diagnostics/query_vectors/q{n}.json').read_text(encoding='utf-8'))
        captured=recorded['vec_question']
        original=np.asarray(json.loads(captured) if isinstance(captured,str) else captured,dtype=float)
        current=np.asarray(query_vector(recorded['question']),dtype=float)
        calibration.append({'diagnostic':f'q{n}','query_cosine_to_captured':float(np.dot(original,current)/(np.linalg.norm(original)*np.linalg.norm(current)))})
    targets={'G02','G15','G16','I01','I02','F03'}
    unchanged=[r for r in workbook if r['id'] not in targets]
    primary_changes=[r['id'] for r in unchanged if r['before_ids']!=r['after_ids']]
    context_changes=[r['id'] for r in unchanged if r['before_context_sha256']!=r['after_context_sha256']]
    assert all(r['after_work_relevant_sources']>0 and r['after_work_relevant_sources']==len(r['after_ids']) for r in work),'Missing or unrelated work source'
    assert not any(r['after_context_has_legacy_arc_fragment'] for r in workbook),'Legacy split ARC fragment in context'
    assert not any(r['after_context_has_known_obsolete_claim'] for r in workbook),'Known obsolete claim in context'
    def golden_allowed(identity):
        return identity is None or all(identity in json.loads(metadata[c]['identities']) or '全體境外生' in json.loads(metadata[c]['identities']) for c in golden)
    applicable_fees=[r for r in fees if golden_allowed(r['identity'])]
    for r in fees:
        r['fixed_handbook_golden_allowed_by_snapshot_identity']=golden_allowed(r['identity'])
        if not r['fixed_handbook_golden_allowed_by_snapshot_identity']:
            assert r['after_fee_table_recall']==0,'Degree-only fee table leaked to another identity'
    summary={'original_tested_items':55,'workbook_retrieval_items':len(workbook),'safety_route_items_tested_separately':8,
             'untargeted_items':len(unchanged),'untargeted_identical_primary':len(unchanged)-len(primary_changes),'untargeted_primary_changes':primary_changes,'untargeted_context_changes':context_changes,
             'work_variants':len(work),'work_relevant_source_coverage_before':sum(r['before_work_relevant_sources']>0 for r in work),
             'work_relevant_source_coverage_after':sum(r['after_work_relevant_sources']>0 for r in work),
             'work_unrelated_selected_before':sum(len(r['before_ids'])-r['before_work_relevant_sources'] for r in work),
             'work_unrelated_selected_after':sum(len(r['after_ids'])-r['after_work_relevant_sources'] for r in work),
             'fee_fixed_golden_applicable_cases':len(applicable_fees),'fee_fixed_golden_full_after':sum(r['after_fee_table_recall']==1 for r in applicable_fees),
             'fee_english_applicable_cases':sum(r['variant']!='Chinese' for r in applicable_fees),
             'fee_english_full_after':sum(r['variant']!='Chinese' and r['after_fee_table_recall']==1 for r in applicable_fees),
             'fee_chinese_applicable_cases':sum(r['variant']=='Chinese' for r in applicable_fees),
             'fee_chinese_full_after':sum(r['variant']=='Chinese' and r['after_fee_table_recall']==1 for r in applicable_fees),
             'fee_cases_without_identity_applicable_golden':len(fees)-len(applicable_fees),
             'legacy_arc_fragment_contexts_after':sum(r['after_context_has_legacy_arc_fragment'] for r in workbook),
             'known_rule_obsolete_contexts_after':sum(r['after_context_has_known_obsolete_claim'] for r in workbook),
             'answer_pass_rate_measured':False}
    report={'kind':'new_controlled_snapshot_rerun_with_actual_existing_embedding_and_memory_pgvector',
            'historical_reconstruction':False,'summary':summary,'workbook_sha256':workbook_hash,'model':manifest,'sql_engine':ready['versions'],
            'kb_snapshot_run':'20261110171206','passages':len(kb),'reembedded_redacted_snapshot':True,
            'identity_and_date_fixed':True,'claude_calls':0,'production_connections':0,
            'workbook_queries':workbook,'fee_variants':fees,'work_variants':work,'model_calibration':calibration,
            'limitations':['No generation or answer pass-rate measurement.','New fixed-snapshot replay; redacted passages and model/runtime may differ from historical production.','No production PostgREST/connection-pool/latency test.','No synthetic policy approvals were added; current regulated claims without review stay excluded.']}
    assert hashlib.sha256(args.workbook.read_bytes()).hexdigest()==workbook_hash
    (REPO/'repair_evidence/real_retrieval_results.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    qcache_path.write_text(json.dumps(qcache),encoding='utf-8')
    print(json.dumps({'workbook_queries':len(workbook),'fees_before_full':sum(r['before_fee_table_recall']==1 for r in fees),'fees_after_full':sum(r['after_fee_table_recall']==1 for r in fees),'fee_cases':len(fees),
                      'work_before_page':sum(r['before_official_work_page'] for r in work),'work_after_page':sum(r['after_official_work_page'] for r in work),'work_cases':len(work)},ensure_ascii=False),flush=True)
finally:
    qcache_path.write_text(json.dumps(qcache),encoding='utf-8')
    try:send({'op':'close'})
    finally:
        engine.stdin.close();engine.wait(timeout=30)
