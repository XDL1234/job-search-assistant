"""桥接协议的身份、单次消费与暂停边界；不访问招聘网站。"""
import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.browser_bridge import BridgeServer
from job_assistant.store import Store
from job_assistant.cli import prepare_config


class BrowserBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.db=Store(self.temp.name);self.addCleanup(self.db.close)
        source=Path(self.temp.name)/'profile.json';source.write_text('{"name":"test"}')
        config=prepare_config({'channels':['boss'],'max_contacts':1,'boss_mode':'filtered','target_roles':['嵌入式'],
                               'profile_path':str(source),'resume_path':str(source)})
        self.run=self.db.create_run(config,'协议测试，不发送')
        self.server=BridgeServer(self.temp.name)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)

    def request(self,path,value=None,**headers):
        data=None if value is None else json.dumps(value).encode()
        headers={'Authorization':'Bearer '+self.server.secret,**headers}
        req=urllib.request.Request(f'http://127.0.0.1:{self.server.server_port}'+path,data,headers)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=4) as response:
            return json.loads(response.read())

    def test_web_origin_and_missing_token_rejected(self):
        for headers in ({'Authorization':''},{'Origin':'https://www.zhipin.com'},{'Origin':'https://evil.example'}):
            with self.assertRaises(urllib.error.HTTPError) as raised:self.request('/status',**headers)
            self.assertEqual(raised.exception.code,403)

    def test_command_is_claimed_once_and_pause_revokes_lease(self):
        command={'id':'test','operation':'observe','run_id':self.run,'expires':time.time()+10,'claimed':False,'event':threading.Event(),'response':None}
        self.server.commands['test']=command
        self.assertEqual(self.request('/next')['id'],'test')
        self.assertIsNone(self.request('/next'))
        self.assertTrue(self.request('/lease/test')['valid'])
        self.db.control(self.run,'paused')
        self.assertFalse(self.request('/lease/test')['valid'])
        self.request('/result',{'id':'test','response':{'ok':True}})
        self.assertTrue(command['event'].is_set())
        self.request('/result',{'id':'test','response':{'ok':False}})
        self.assertTrue(command['response']['ok'],'迟到结果不能覆盖已记录结果')

    def test_expired_commands_and_unknown_operations_rejected(self):
        self.server.commands['old']={'id':'old','run_id':self.run,'expires':1,'claimed':False}
        self.assertIsNone(self.request('/next'))
        with self.assertRaises(urllib.error.HTTPError):self.request('/command',{'operation':'shell','run_id':self.run})

    def test_connection_status_requires_extension_heartbeat(self):
        from job_assistant.browser_bridge import connection_status
        self.assertFalse(connection_status(self.temp.name)['connected'])
        self.request('/next')
        self.assertTrue(connection_status(self.temp.name)['connected'])

    def test_commit_needs_claimed_action_and_material_change_revokes_lease(self):
        command={'id':'send','operation':'greet','run_id':self.run,'expires':time.time()+10,'claimed':False}
        self.assertFalse(self.server.valid_command(command))
        command['operation']='observe'
        self.assertTrue(self.server.valid_command(command))
        (Path(self.temp.name)/'profile.json').write_text('{"name":"changed"}')
        self.assertFalse(self.server.valid_command(command))


if __name__=='__main__':unittest.main()
