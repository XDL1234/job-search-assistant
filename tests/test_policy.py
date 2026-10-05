import importlib.util
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))


class PolicyTests(unittest.TestCase):
    def decide(self, message, **kwargs):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.policy'), '回复路由尚未实现')
        from job_assistant.policy import decide_reply
        return decide_reply(message, **kwargs)

    def test_mixed_interview_question_always_hands_off_entire_message(self):
        result = self.decide('税前12k，深圳南山，周五下午3点来面试可以吗？', mode='ai',
                             analysis={'categories': ['salary', 'location'], 'complete': True}, rules=[])
        self.assertEqual(result['action'], 'human')
        self.assertEqual(result['reason'], 'interview')

    def test_unknown_or_ambiguous_parameters_never_get_acceptance(self):
        rules = [{'id': 'salary', 'category': 'salary', 'when': {'salary_monthly_gross': {'gte': 15000}}, 'reply': '可以接受。'}]
        for facts in ({}, {'salary_monthly_gross': 12000}, {'salary_monthly_gross': '15k'}):
            result = self.decide('薪资能接受吗？', mode='ai', rules=rules,
                                 analysis={'categories': ['salary'], 'complete': True, 'facts': facts})
            self.assertEqual(result['action'], 'human')

    def test_matching_template_is_not_rewritten(self):
        result = self.decide('税前月薪16k，可以接受吗？', mode='human',
                             analysis={'categories': ['salary'], 'complete': True, 'facts': {'salary_monthly_gross': 16000}},
                             rules=[{'id': 's1', 'category': 'salary', 'when': {'salary_monthly_gross': {'gte': 15000}}, 'reply': '这个薪资范围可以接受，谢谢。'}])
        self.assertEqual(result['text'], '这个薪资范围可以接受，谢谢。')

    def test_contradicting_rules_and_partial_multi_questions_handoff(self):
        rules = [{'id': 'a', 'category': 'location', 'when': {'city': {'eq': '深圳'}}, 'reply': '可以。'},
                 {'id': 'b', 'category': 'location', 'when': {'city': {'eq': '深圳'}}, 'reply': '不接受。'}]
        result = self.decide('深圳可以吗？', mode='ai', rules=rules,
                             analysis={'categories': ['location'], 'complete': True, 'facts': {'city': '深圳'}})
        self.assertEqual(result['action'], 'human')
        result = self.decide('深圳可以吗，多久到岗？', mode='ai', rules=rules[:1],
                             analysis={'categories': ['location', 'availability'], 'complete': True, 'facts': {'city': '深圳'}})
        self.assertEqual(result['action'], 'human')

    def test_ai_answer_needs_cited_profile_facts_and_no_commitment(self):
        args = {'mode': 'ai', 'rules': [], 'profile': {'skills': ['C', 'STM32']},
                'analysis': {'categories': ['other'], 'complete': True, 'facts': {}, 'new_commitment': False}}
        self.assertEqual(self.decide('你用过什么芯片？', draft='我用过 STM32。', sources=['skills'], **args)['action'], 'reply')
        self.assertEqual(self.decide('你用过什么芯片？', draft='我用过 FPGA。', sources=['missing'], **args)['action'], 'human')
        args['analysis']['new_commitment'] = True
        self.assertEqual(self.decide('能保证周末都上班吗？', draft='可以。', sources=['skills'], **args)['action'], 'human')

    def test_resume_request_not_generated_as_text(self):
        result = self.decide('方便发一下附件简历吗？', mode='ai', rules=[],
                             analysis={'categories': ['resume'], 'complete': True})
        self.assertEqual(result['action'], 'resume')

    def test_filter_mode_does_not_require_description(self):
        self.assertIsNotNone(importlib.util.find_spec('job_assistant.policy'), '岗位筛选尚未实现')
        from job_assistant.policy import select_job
        jobs = [{'key': '1', 'title': '开发工程师', 'contacted': False}, {'key': '2', 'contacted': True}]
        self.assertEqual(select_job(jobs, 'filtered')['key'], '1')
        jobs[0]['evaluation'] = {'hard_conditions': ['unknown'], 'fit': 90}
        self.assertIsNone(select_job(jobs, 'screened'))
        jobs[0]['evaluation']['hard_conditions'] = ['yes']
        self.assertEqual(select_job(jobs, 'screened')['key'], '1')


if __name__ == '__main__':
    unittest.main()
