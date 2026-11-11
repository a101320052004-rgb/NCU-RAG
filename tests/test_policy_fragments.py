"""Split policy fragments and independent manual FAQ boundaries."""
import unittest
from unittest.mock import Mock, patch
import test_reliability as base

class PolicyFragmentTests(unittest.TestCase):
    def test_inherited_obsolete_rule_blocks_fragment(self):
        text='n 15 days. Required ARC documents'
        md=base.row(text,policy_context_rule='obsolete:arc_arrival_deadline')
        self.assertFalse(base.policy.eligible(text,md,identity='交換生',topic='住',today=base.TODAY,stale_before=base.CUTOFF))

    def test_local_store_checks_neighboring_crawler_policy(self):
        rag=base.rag_module()
        previous=base.row('ARC apply within 15 days after arrival.',id='pdf-0')
        collection=Mock()
        collection.get.return_value={'ids':['pdf-0'],'documents':[previous['body']],'metadatas':[previous]}
        with patch.object(rag,'collection',return_value=collection):
            enriched=rag._local_policy_rows([('pdf-1','n 15 days.',base.row())])
        self.assertFalse(rag._eligible(enriched[0][1],enriched[0][2],'交換生','住'))

    def test_manual_faq_does_not_expand_into_another_question(self):
        rag=base.rag_module()
        hit={'text':'One complete FAQ answer','meta':base.row(id='mdmanual-1'),'distance':.1,'score':.1}
        with patch.object(rag,'_fetch_chunks',return_value={}) as fetch:
            result=rag.expand_context([hit],'交換生','住')
        fetch.assert_called_once_with([],'交換生','住')
        self.assertEqual(result[0]['text'],hit['text'])

class ARCDeadlineEvidenceTests(unittest.TestCase):
    def test_unrelated_arc_mentions_cannot_trigger_generation(self):
        rag=base.rag_module()
        rag.contacts.contact_line.return_value='Official contact'
        unrelated=base.row('Copy of ARC required for a scholarship application',id='faq-1')
        found=[(unrelated['body'],unrelated,.1)]
        with patch.object(rag,'_partner_hits',return_value=[]), patch.object(rag,'_query_supabase',return_value=found), \
             patch.object(rag.embedder,'embed_query',return_value=[.1]), patch.object(rag,'call_llm') as llm:
            result=rag.answer('Is the 15-day ARC application deadline still valid?','外籍學位生','en')
        self.assertEqual(result['route'],'no_data')
        llm.assert_not_called()
        rag.db.llm_allow.assert_not_called()

    def test_reviewed_complete_current_deadline_can_be_retrieved(self):
        rag=base.rag_module()
        text='ARC holders must apply within 30 days after arrival.'
        md=base.row(text,policy_review_status='approved',policy_reviewed_at='2026-11-10',policy_reviewed_sha256=base.policy.body_hash(text))
        with patch.object(rag,'_partner_hits',return_value=[]), patch.object(rag,'_query_supabase',return_value=[(text,md,.1)]), \
             patch.object(rag.embedder,'embed_query',return_value=[.1]):
            found=rag.retrieve('Is the 15-day ARC application deadline still valid?','外籍學位生','en')
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]['text'],text)

if __name__=='__main__':unittest.main()
