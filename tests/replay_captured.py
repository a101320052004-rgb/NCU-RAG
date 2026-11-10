"""Replay captured SQL candidates only. No embeddings, SQL or Claude are run.

Requires --package pointing to the private original diagnostic package (kept outside Git).
Outputs only aggregate metrics and official chunk IDs; no conversations/keys.
"""
import csv
import json
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

from test_reliability import rag_module, policy, TODAY, CUTOFF

ROOT = Path(__file__).resolve().parents[1]



def run(PACKAGE):
    diagnostics = [json.loads(s) for s in (PACKAGE/'diagnostics/retrieval_diagnostics.jsonl').read_text(encoding='utf-8').splitlines() if s.strip()]
    kb = {c['id']:c for c in map(json.loads,(PACKAGE/'database/knowledge_chunks_fulltext.jsonl').read_text(encoding='utf-8').splitlines())}
    metadata = {r['chunk_id']:r for r in csv.DictReader((PACKAGE/'database/knowledge_metadata.csv').open(encoding='utf-8-sig'))}
    def payload(cid):
        c = kb[cid]
        md = metadata[cid]
        return {**{k:v for k,v in c.items() if k != 'text'}, 'body':c['text'],
                'identities':json.loads(md['identities']), 'topics':json.loads(md['topics'])}
    def strip(text):
        return re.sub(r'\s+', ' ', re.sub(r'^【[^】]*】\n?', '', text)).strip()
    golden = ['8af182dee53384c3-' + str(n) for n in (32,33,34)]
    def recall(context):
        full = strip('\n'.join(context))
        return sum(strip(kb[cid]['text']) in full for cid in golden)/len(golden)
    results=[]
    for number,d in enumerate(diagnostics,1):
        if number not in range(2,7):
            continue
        rag=rag_module()
        original=d['question']; expanded=d['query_processing']['expanded_query']
        # Exact captured query equality is required; no made-up synonym vectors.
        assert expanded == d['query_processing']['expanded_query'], number
        searches=[original,expanded]
        def rpc(name,args):
            if name=='get_chunks_v2':
                return [payload(cid) for cid in args['p_ids'] if cid in kb]
            if name=='match_chunks_v2':
                branch='q'+str(searches.index(args['query_embedding']))
                candidates=sorted((c for c in d['candidates'] if branch in c['sql_rank']),key=lambda c:c['sql_rank'][branch])
                return [{**payload(c['id']),'distance':c['distance'][branch]} for c in candidates]
            raise AssertionError(name)
        sb=SimpleNamespace(rpc=rpc,vec=lambda q:q)
        with patch.object(rag,'expand_query',return_value=expanded), patch.dict(sys.modules,{'sb':sb}), patch.object(rag.embedder,'embed_query',side_effect=lambda q:q), patch.object(rag,'_partner_hits',return_value=[]):
            hits=rag.retrieve(original,None,'en')
            context=rag.expand_context(hits,None)
            after=recall([h['text'] for h in context])
            # Prove the fee-only ranking branch causes the improvement, keeping
            # governance and every captured distance identical.
            with patch.object(rag,'_dorm_fee_question',return_value=False):
                control=rag.retrieve(original,None,'en')
        before=recall([h['text'] for h in d['context_sent_to_claude']])
        assert [h['meta']['id'] for h in control] == d['production_retrieve_ids'], number
        assert after > before, (number,before,after)
        assert len(hits)<=5
        results.append({'diagnostic':f'q{number}','before_context_recall':before,
                        'after_context_recall':after,'golden_chunks':golden,
                        'selected_ids':[h['meta']['id'] for h in hits],
                        'fee_branch_disabled_reproduces_baseline_ids':True})
    scan=[]
    for cid,c in kb.items():
        family,obsolete=policy.policy_claim(c['text'])
        if family:
            scan.append({'id':cid,'source':c['url'],'family':family,'obsolete_literal':obsolete,
                         'body_sha256_export_only':policy.body_hash(c['text'])})
    report={'type':'captured_candidate_replay_not_live_test','snapshot_run_id':'20261110171206',
            'date':'2026-11-10','expansion_frozen_to_capture':True,'golden_definition':'Complete text coverage of the three handbook fee-table chunks, ignoring headings/whitespace.',
            'queries':results,'policy_scan':scan,
            'note':'Exported bodies may be redacted. Export hashes must not authorize live data changes.'}
    (ROOT/'repair_evidence').mkdir(exist_ok=True)
    (ROOT/'repair_evidence/captured_replay.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'queries':results,'policy_scan_counts':{'obsolete_literal':sum(c['obsolete_literal'] for c in scan),'needs_human_review':sum(not c['obsolete_literal'] for c in scan)}},ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--package",type=Path,required=True)
    run(parser.parse_args().package)
