"""回复配置保存不发送，面试优先且已有轮次规则不被改写。"""
import sys
from pathlib import Path
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.store import Store
from job_assistant.app_service import handle_app_request

class ReplySettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Store(self.temp.name);self.addCleanup(self.db.close)
    def call(self,method,**params):return handle_app_request(self.db,{'id':'x','method':method,'params':params})
    def test_settings_reject_salary_without_conditions(self):
        value={'unknown_reply_mode':'human','rules':[{'id':'salary','category':'salary','reply':'可以','when':{},'enabled':True}]}
        self.assertFalse(self.call('save_reply_settings',settings=value)['ok'])
        value['rules'][0]['when']={'salary_monthly_gross':{'gte':5000}}
        self.assertTrue(self.call('save_reply_settings',settings=value)['ok'])
        self.assertEqual(self.call('get_reply_settings')['result'],value)
    def test_interview_wins_and_saving_never_creates_actions(self):
        value={'unknown_reply_mode':'ai','rules':[{'id':'location','category':'location','reply':'可以在青岛工作','when':{'city':{'eq':'青岛'}},'enabled':True}]}
        self.assertTrue(self.call('save_reply_settings',settings=value)['ok'])
        result=self.call('preview_reply',text='在青岛工作可以吗，周五来面试？',analysis={'categories':['location','interview'],'complete':True,'facts':{'city':'青岛'}})
        self.assertEqual(result['result']['action'],'human')
        self.assertEqual(self.db.conn.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)
    def test_duplicate_rule_ids_rejected(self):
        rule={'id':'x','category':'availability','reply':'以实际沟通为准','when':{},'enabled':False}
        self.assertFalse(self.call('save_reply_settings',settings={'unknown_reply_mode':'human','rules':[rule,rule]})['ok'])

if __name__=='__main__':unittest.main()
