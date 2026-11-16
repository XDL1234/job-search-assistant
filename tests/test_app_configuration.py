"""真实材料配置：职业类型、投递渠道、执行模式与票据边界。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.app_service import handle_app_request
from job_assistant.store import Store


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.db=Store(self.root/'data');self.addCleanup(self.db.close)
        source=self.root/'profile.json';source.write_text('{"name":"测试","target_role":"嵌入式"}',encoding='utf-8')
        extraction=self.call('extract_profile',path=str(source))['result']
        self.assertTrue(self.call('save_profile',extraction=extraction,profile=extraction['profile'],resume_path=str(source),confirmed=True)['ok'])

    def call(self,method,**params):
        return handle_app_request(self.db,{'id':'x','method':method,'params':params})

    def config(self,**extra):
        return {'channels':['boss'],'employment_type':'internship','target_roles':['嵌入式'],'boss_mode':'filtered',
                'execution_mode':'script','max_contacts':3,'filters':'沿用当前 BOSS 筛选','window_title':'BOSS直聘',**extra}

    def test_employment_and_execution_modes_are_preserved(self):
        for kind in ('internship','campus','full_time'):
            for boss,engine in [('filtered','script'),('filtered','ai'),('screened','ai')]:
                result=self.call('preview_run',config=self.config(employment_type=kind,boss_mode=boss,execution_mode=engine))
                self.assertTrue(result['ok'],result)
                self.assertEqual(result['result']['config']['employment_type'],kind)
                start=self.call('start_run',ticket=result['result']['ticket'],confirmed=True)
                self.assertTrue(start['ok'],start)
                config=json.loads(self.db.one('runs',start['result']['run_id'])['config'])
                self.assertEqual(config['execution_mode'],engine)
                self.assertFalse(config['simulation'])

    def test_unsafe_or_unsupported_configuration_is_rejected(self):
        for change in [{'employment_type':'anything'},{'max_contacts':0},{'execution_mode':'shell'},
                       {'boss_mode':'screened','execution_mode':'script'},{'window_title':''},{'filters':'只接受上海'}]:
            self.assertFalse(self.call('preview_run',config=self.config(**change))['ok'],change)

    def test_web_preview_preserves_rows_and_table_change_invalidates_ticket(self):
        table=self.root/'companies.csv';table.write_text('公司名称,申请网站\n测试公司,https://example.org/apply\n',encoding='utf-8')
        config=self.config(channels=['web'],company_table=str(table),execution_mode='ai')
        result=self.call('preview_run',config=config)
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['result']['config']['company_rows'][0]['company'],'测试公司')
        table.write_text('公司名称,申请网站\n其他公司,https://example.org\n',encoding='utf-8')
        self.assertFalse(self.call('start_run',ticket=result['result']['ticket'],confirmed=True)['ok'])


if __name__=='__main__':unittest.main()
