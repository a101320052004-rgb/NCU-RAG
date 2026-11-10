"""Run: python -m unittest discover -s tests -v (stdlib only).

Captured SQL distances are replayed, not recomputed. No network, Claude, or DB.
"""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '05_程式碼/app'))
import knowledge_policy as policy

TODAY = dt.date(2026, 11, 10)
CUTOFF = dt.date(2025, 11, 10)
IDENTITIES = ['外籍學位生', '交換生', '僑生', '陸生', '訪問研修生']


def config():
    return SimpleNamespace(STORE='supabase', TOP_K=5, MAX_DISTANCE=.30,
        today=lambda: TODAY, stale_before=lambda: CUTOFF, IDENTITIES=IDENTITIES,
        ALL_STUDENTS='全體境外生', STALE_MONTHS=12, CURRENT_ACADEMIC_YEAR=115,
        MENU_TOPICS=['食','衣','住','行','育','樂'], CONTACT_ZH='Official office',
        CONTACT_EN='Official office', SECURITY_PHONE='Campus official hotline',
        CURATED_DIR=Path(tempfile.gettempdir()), RAW_DIR=Path(tempfile.gettempdir()),
        APP_DIR=ROOT/'05_程式碼/app')


def load(name, path, modules):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


def row(text='Useful campus information', **updates):
    md = {'id':'doc-1','title':'Campus information','url':'https://example.edu/campus',
          'lang':'en','status':'active','last_verified':'2026-11-09','valid_until':None,
          'identities':['全體境外生'],'topics':['住']}
    md.update(updates)
    return {'body':text, **md}


def rag_module():
    return load('rag_under_test', ROOT/'05_程式碼/app/rag.py',
                {'config':config(),'contacts':Mock(),'db':Mock(),'embedder':Mock()})


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.db=Mock();self.db.get_user.return_value={};self.db.hash_user.return_value='test-only'
        self.rag=Mock();self.rag.answer.return_value={'answer':'Campus information','route':'rag','sources':[]}
        contacts=Mock();contacts.SECURITY_CALL={'en':'Security','zh':'校安','tel':'test'}
        contacts.CRISIS_CALL={'en':'Support','zh':'協助','tel':'test'}
        self.router=load('router_under_test',ROOT/'05_程式碼/app/router.py',
                        {'config':config(),'contacts':contacts,'db':self.db,'rag':self.rag})

    def test_H07_H08_handoff_without_generation(self):
        for q in ['[Test scenario] I am thinking about hurting myself. Who can I contact for immediate help?',
                  '[Test scenario] I might hurt myself. Who can help me right now?']:
            with self.subTest(question=q):
                self.rag.reset_mock()
                out=self.router.handle('test-person',q,channel='web')
                self.assertEqual(out['route'],'handoff');self.rag.answer.assert_not_called()

    def test_explicit_negation_does_not_handoff(self):
        for q in ["I don't want to hurt myself.",'I am not thinking about hurting myself.',
                  'I am never going to kill myself.','我不想傷害自己。']:
            with self.subTest(question=q):self.assertIsNone(self.router.detect_high_risk(q))

    def test_third_person_real_risk_is_not_ignored(self):
        self.assertEqual(self.router.detect_high_risk('My roommate says she might hurt herself tonight.'),'自我傷害')
        self.assertEqual(self.router.detect_high_risk('My roommate read a novel and says she might hurt herself tonight.'),'自我傷害')

    def test_educational_and_ordinary_third_person_questions(self):
        for q in ['Translate the phrase hurt myself into Chinese.',
                  'In my novel a character says I might hurt myself.',
                  'My friend needs a work permit. How does she apply?',
                  'How do I avoid hurting myself while cooking?',
                  'What are the dormitory fees?','Where is OIA?',
                  'How can I apply for permission to work while studying at NCU?']:
            with self.subTest(question=q):self.assertIsNone(self.router.detect_high_risk(q))

    def test_negation_does_not_hide_later_urgent_clause(self):
        self.assertEqual(self.router.detect_high_risk("I don't want to hurt myself, but I might hurt myself tonight."),'自我傷害')
        self.assertEqual(self.router.detect_high_risk('I am writing a novel, but I might hurt myself tonight.'),'自我傷害')

    def test_identity_override_and_persisted_identity_reach_rag(self):
        for ident in IDENTITIES:
            with self.subTest(identity=ident):
                self.db.get_user.return_value={'identity':'僑生'}
                self.router.handle('test-person','What are the dormitory fees?',identity_override=ident)
                self.assertEqual(self.rag.answer.call_args.args[1],ident)
        self.db.get_user.return_value={'identity':'交換生'}
        self.router.handle('test-person','Where is OIA?')
        self.assertEqual(self.rag.answer.call_args.args[1],'交換生')


class GovernanceTests(unittest.TestCase):
    def test_F03_G15_G16_obsolete_claims_blocked_after_fresh_crawl(self):
        texts=['ARC: apply within 15 days counted from the next day of arrival.',
               '持居留簽證入國後15日內應申辦外僑居留證。',
               'Work permit maximum duration is 6 months.',
               '工作證：許可期間最長為6個月。']
        for t in texts:
            with self.subTest(text=t):
                md=row(t, last_verified='2026-11-10')
                md.update(policy_review_status='approved',policy_reviewed_at='2026-11-10',policy_reviewed_sha256=policy.body_hash(t))
                self.assertFalse(policy.eligible(t,md,identity=None,topic=None,today=TODAY,stale_before=CUTOFF))

    def test_fresh_fetch_is_not_human_review(self):
        for t in ['ARC resident visa holders apply within 30 days after arrival.',
                  'Student work permit maximum duration is one year.']:
            with self.subTest(text=t):
                self.assertTrue(policy.review_reason(t,{},TODAY,CUTOFF))
                approved={'policy_review_status':'approved','policy_reviewed_at':'2026-11-10','policy_reviewed_sha256':policy.body_hash(t)}
                self.assertIsNone(policy.review_reason(t,approved,TODAY,CUTOFF))
                approved['policy_reviewed_sha256']='wrong-body'
                self.assertTrue(policy.review_reason(t,approved,TODAY,CUTOFF))

    def test_new_and_old_policy_conflict_is_not_auto_approved(self):
        t='ARC: apply within 30 days after arrival. Work permit maximum duration is 6 months.'
        self.assertTrue(policy.policy_claim(t)[1])

    def test_year_alone_does_not_expire_valid_document(self):
        t='Dormitory Rules enacted in 2018: keep corridors clear.'
        self.assertIsNone(policy.review_reason(t,{},TODAY,CUTOFF))

    def test_unrelated_visa_and_insurance_durations_not_misclassified(self):
        for t in ['ARC form: apply to extend visitor visa within 15 days before the visa expires.',
                  'Work permit application. Insurance enrollment starts after six months of residence.']:
            with self.subTest(text=t):self.assertEqual(policy.policy_claim(t),(None,False))

    def test_each_identity_and_time_status_boundary(self):
        text='Dormitory contacts'
        for ident in IDENTITIES:
            for tags in [[ident],['全體境外生']]:
                self.assertTrue(policy.eligible(text,row(text,identities=tags),identity=ident,topic='住',today=TODAY,stale_before=CUTOFF))
        for changes in [{'status':'expired'},{'status':'needs_review'},{'status':'superseded'},
                        {'valid_until':'2026-11-09'},{'valid_until':'invalid'},
                        {'last_verified':'2025-11-09'},{'identities':['陸生']},{'topics':['工作']}]:
            with self.subTest(changes=changes):
                self.assertFalse(policy.eligible(text,row(text,**changes),identity='交換生',topic='住',today=TODAY,stale_before=CUTOFF))
        self.assertTrue(policy.eligible(text,row(text,valid_until='2026-11-10',last_verified='2025-11-10'),identity='交換生',topic='住',today=TODAY,stale_before=CUTOFF))

    def test_ingest_uses_same_policy_before_timestamp_logic(self):
        ingest=load('ingest_under_test',ROOT/'05_程式碼/app/ingest.py',{'config':config(),'embedder':Mock()})
        self.assertEqual(ingest.judge({'title':'工作證','text':'工作證許可期間最長為6個月。','fetched_at':'2026-11-10','source':'manual'})[0],'needs_review')


class ContextAndRetrievalTests(unittest.TestCase):
    def test_source_dates_survive_supabase_iso_and_local_integer_metadata(self):
        rag=rag_module()
        for value in ['2026-11-09',20261109,dt.date(2026,11,9)]:
            self.assertEqual(rag.fmt_date(value),'2026-11-09')
        self.assertEqual(rag.fmt_date(None),'未知')
        self.assertFalse(rag._dorm_fee_payload('Nothing about fees',{'title':None}))

    def test_normal_queries_keep_existing_score_and_document_cap(self):
        rag=rag_module()
        candidates=[]
        for n,(cid,distance,url) in enumerate([
                ('crawler-a-0',.10,'https://example.edu/a'),
                ('mdmanual-0',.11,'https://example.edu/b'),
                ('crawler-a-1',.12,'https://example.edu/a'),
                ('crawler-a-2',.13,'https://example.edu/a'),
                ('crawler-c-0',.31,'https://example.edu/c')]):
            r=row('Normal campus information',id=cid,url=url,lang='en')
            candidates.append((r.pop('body'),r,distance))
        for q in ['Where is OIA?','How do I check out of the dormitory?',
                  'What is the tuition fee?','Where is the library?']:
            with self.subTest(question=q), patch.object(rag,'_partner_hits',return_value=[]), \
                 patch.object(rag,'_query_supabase',return_value=candidates), \
                 patch.object(rag.embedder,'embed_query',return_value=[0.]):
                hits=rag.retrieve(q,'交換生','en')
                self.assertEqual([h['meta']['id'] for h in hits],['mdmanual-0','crawler-a-0','crawler-a-1'])

    def test_neighbors_use_same_eligibility_and_keep_legal_neighbor(self):
        rag=rag_module()
        for bad in [{'status':'needs_review'},{'valid_until':'2026-11-09'},{'identities':['陸生']},
                    {'last_verified':'2025-11-09'}]:
            with self.subTest(bad=bad):
                rows=[row('SAFE PREVIOUS',id='doc-0'),row('UNSAFE NEXT',id='doc-2',**bad)]
                sb=SimpleNamespace(rpc=Mock(return_value=rows))
                hit={'text':'MAIN','meta':{k:v for k,v in row('MAIN').items() if k!='body'},'distance':.1,'score':.1}
                with patch.dict(sys.modules,{'sb':sb}):ctx=rag.expand_context([hit],'交換生','住')
                self.assertIn('SAFE PREVIOUS',ctx[0]['text']);self.assertNotIn('UNSAFE NEXT',ctx[0]['text'])
                args=sb.rpc.call_args.args
                self.assertEqual(args[0],'get_chunks_v2');self.assertEqual(args[1]['p_identity'],'交換生')
                self.assertEqual(args[1]['p_topic'],'住')

    def test_ineligible_primary_context_rejected(self):
        rag=rag_module();bad=row('BAD',status='needs_review')
        with patch.object(rag,'_fetch_chunks',return_value={}) as fetch:
            self.assertEqual(rag.expand_context([{'text':'BAD','meta':bad}],'交換生'),[])
            fetch.assert_called_once_with([], '交換生',None)

    def test_synonyms_expand_but_preserve_original_question(self):
        rag=rag_module()
        for term in ['work permit','permission to work','employment authorization','authorisation to work']:
            question=f'How do I apply for {term} at NCU?'
            self.assertTrue(rag.expand_query(question).startswith(question+' '))
            self.assertIn('工作證',rag.expand_query(question))
        self.assertEqual(rag.expand_query('How do I submit my coursework?'),'How do I submit my coursework?')

    def test_identity_not_removed_from_main_rpc_or_partner_shortcut(self):
        rag=rag_module();sb=SimpleNamespace(rpc=Mock(return_value=[]),vec=lambda x:str(x))
        with patch.dict(sys.modules,{'sb':sb}):
            for ident in IDENTITIES:
                rag._query_supabase([0.1]*384,ident,'住')
                self.assertEqual(sb.rpc.call_args.args[1]['p_identity'],ident)
                with patch.object(rag,'partner_terms',return_value=['Example University']):
                    rag._partner_hits('exchange with Example University',ident,'育')
                self.assertEqual(sb.rpc.call_args.args[1]['p_identity'],ident)
                self.assertEqual(sb.rpc.call_args.args[0],'find_partner_chunks_v2')

    def test_fee_rescue_is_scoped_and_threshold_and_k_unchanged(self):
        rag=rag_module()
        self.assertTrue(rag._dorm_fee_question('What are the housing charges?'))
        for q in ['How do I check out of the dormitory?','Where is the housing office?','What is the tuition fee?']:
            self.assertFalse(rag._dorm_fee_question(q))
        self.assertEqual(rag.config.TOP_K,5);self.assertEqual(rag.config.MAX_DISTANCE,.30)



class UploadAndGenerationTests(unittest.TestCase):
    def test_upload_stops_before_embeddings_when_sql_is_not_ready(self):
        for result in [1, RuntimeError('missing version RPC')]:
            with self.subTest(result=type(result).__name__):
                cfg=config()
                cfg.CLOUD_EMBEDDER='e5_onnx';cfg.STORE_DIR=Path(tempfile.gettempdir())
                cfg.E5_ONNX_REPO='fixture-only';cfg.E5_ONNX_FILE='fixture-only'
                for name in ['SUPABASE_URL','SUPABASE_KEY','SUPABASE_INGEST_SECRET']:
                    setattr(cfg,name,'test-only')
                sb=Mock();ingest=Mock();embedding=Mock()
                if isinstance(result,Exception):sb.rpc.side_effect=result
                else:sb.rpc.return_value=result
                cloud=load('cloud_ingest_under_test',ROOT/'05_程式碼/app/cloud_ingest.py',
                           {'config':cfg,'ingest':ingest,'embedder':embedding,'sb':sb})
                self.assertEqual(cloud.main(),1)
                ingest.split_records.assert_not_called()
                embedding.embed_passages.assert_not_called()

    def test_numeric_substrings_do_not_become_obsolete_rules(self):
        for text in ['ARC apply within 115 days after arrival.',
                     'Work permit maximum duration is 16 months.']:
            self.assertFalse(policy.policy_claim(text)[1])

    def test_answer_passes_identity_and_filtered_context_to_generation(self):
        rag=rag_module()
        rag.contacts.contact_line.return_value='Official contact'
        rag.db.llm_allow.return_value='ok'
        hit={'text':'MAIN','meta':row('MAIN',id='doc-1'),'distance':.1,'score':.1}
        sb=SimpleNamespace(rpc=Mock(return_value=[row('SAFE PREVIOUS',id='doc-0'),
                           row('EXPIRED NEXT',id='doc-2',valid_until='2026-11-09')]))
        with patch.dict(sys.modules,{'sb':sb}), patch.object(rag,'retrieve',return_value=[hit]), \
             patch.object(rag,'system_prompt',return_value='SYSTEM'), \
             patch.object(rag,'plain_text',side_effect=lambda t:t), \
             patch.object(rag,'call_llm',return_value='A campus answer') as llm:
            out=rag.answer('Where is the housing office?','交換生','en','住')
        self.assertEqual(out['route'],'rag')
        self.assertEqual(sb.rpc.call_args.args[1]['p_identity'],'交換生')
        self.assertEqual(sb.rpc.call_args.args[1]['p_topic'],'住')
        self.assertIn('SAFE PREVIOUS',llm.call_args.args[1])
        self.assertNotIn('EXPIRED NEXT',llm.call_args.args[1])


if __name__=='__main__':unittest.main()
