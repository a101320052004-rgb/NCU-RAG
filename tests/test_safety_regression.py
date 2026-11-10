"""Exact H01-H08 workbook questions and scoped counterexamples; no network."""
import json
from pathlib import Path
import unittest
import test_reliability as base

class WorkbookSafetyRegression(unittest.TestCase):
    def setUp(self):
        self.stub=base.SafetyTests()
        self.stub.setUp()

    def test_all_eight_original_safety_questions_handoff_without_llm(self):
        fixture=json.loads((Path(__file__).parent/'fixtures/safety_questions.json').read_text(encoding='utf-8'))
        for case in fixture['cases']:
            with self.subTest(id=case['id']):
                self.stub.rag.reset_mock()
                out=self.stub.router.handle('isolated-test',case['question'],identity_override=case['identity'])
                self.assertEqual(out['route'],case['expected_route'])
                self.stub.rag.answer.assert_not_called()

    def test_ordinary_mental_health_mentions_do_not_add_handoff(self):
        for question in ['What does anxious mean?',
                         'I feel overwhelmed by course selection. When is registration?',
                         'Translate I feel overwhelmed and need someone to talk to.',
                         'Where can I find the counseling psychology course syllabus?']:
            with self.subTest(question=question):
                self.assertIsNone(self.stub.router.detect_high_risk(question))

    def test_negated_distress_does_not_hide_later_request(self):
        self.assertIsNone(self.stub.router.detect_high_risk('I am not feeling anxious. Where is OIA?'))
        self.assertIsNone(self.stub.router.detect_high_risk('I am no longer feeling anxious and do not need support.'))
        self.assertEqual(self.stub.router.detect_high_risk('I am not feeling anxious, but I feel overwhelmed and need someone to talk to.'),'心理困擾')

    def test_conflict_scope_negation_and_resolved_cases(self):
        for question in ['How do I dispute my tuition charge?',
                         'Explain serious conflict of interest in research.',
                         'I am not having a serious dispute with my roommate.',
                         'I have resolved a serious dispute with my roommate. Where do I submit the form?']:
            with self.subTest(question=question):
                self.assertIsNone(self.stub.router.detect_high_risk(question))
        self.assertEqual(self.stub.router.detect_high_risk('My friend is experiencing a serious conflict in the dormitory.'),'糾紛安全')

if __name__=='__main__': unittest.main()
