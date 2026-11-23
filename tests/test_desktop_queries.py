"""用历史和跨轮数据验证桌面分区查询及记录分页。"""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.app_service import handle_app_request
from job_assistant.store import Store


class DesktopQueryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(folder.name)
        self.addCleanup(self.store.close)
        self.db = self.store.conn
        self.run = self.store.create_run({'channels': ['web'], 'target_roles': ['测试'],
                                          'profile_path': '私有文件'}, '测试')

    def request(self, method, **params):
        return handle_app_request(self.store, {'id': 'test', 'method': method, 'params': params})

    def result(self, method, **params):
        response = self.request(method, **params)
        self.assertTrue(response['ok'], response)
        return response['result']

    def attempt(self, key, run_id=None, status='success', moment='2026-08-08T12:00:00Z', **overrides):
        values = dict(id=key, run_id=run_id or self.run, company='普通公司', job='工程师', url='https://example.test',
                      channel='web', job_key=key, status=status, reason='填写', started=moment, updated=moment)
        self.db.execute('INSERT INTO attempts VALUES (:id,:run_id,:company,:url,:job,:channel,:job_key,'
                        ':status,:reason,:started,:updated)', values | overrides)

    def test_pagination_global_sort_filter_literal_search_and_details(self):
        with self.db:
            for i in range(110):
                self.attempt(f'r{i:03}', channel='boss' if i < 10 else 'web')
            self.attempt('target', company='MÜNCHEN 10%_芯片', status='uncertain', moment='2026-01-01T01:02:03Z')
            self.db.execute("INSERT INTO fields VALUES ('target','学校','已填写内容','profile.school',NULL)")
            self.db.execute("INSERT INTO evidence VALUES ('e','target','result','本机文件','hash','2026-01-01')")
            self.db.execute("""INSERT INTO actions VALUES ('act',?,'target','submit','收件人','原始私有载荷',
                'key','uncertain',NULL,'回执待核查','2026-01-01','2026-01-01')""", (self.run,))
        first = self.result('list_records', page_size=50)
        last = self.result('list_records', page_size=50, page=999)
        self.assertEqual((first['total'], first['records'][0]['id'], len(first['records'])), (111, 'r109', 50))
        self.assertEqual((last['page'], len(last['records']), last['records'][-1]['id']), (3, 11, 'target'))
        self.assertFalse(set(row['id'] for row in last['records']) & {row['id'] for row in first['records']})
        self.assertNotIn('url', first['records'][0])
        self.assertNotIn('fields', first)
        self.assertEqual(self.result('list_records', query='münchen 10%_', channel='web', status='uncertain',
                                     sort='oldest')['records'][0]['id'], 'target')
        self.assertEqual(self.result('list_records', query='10___')['total'], 0)
        self.assertEqual(self.result('list_records', channel='boss')['total'], 10)
        self.assertEqual(self.result('list_records', query='无匹配', page=2)['page'], 1)
        detail = self.result('record_detail', attempt_id='target')
        self.assertEqual(detail['attempt']['reason'], '填写')
        self.assertEqual(detail['fields'][0]['value'], '已填写内容')
        self.assertEqual(detail['actions'][0]['status'], 'uncertain')
        self.assertNotIn('path', detail['evidence'][0])
        self.assertNotIn('payload', detail['actions'][0])
        for params in ({'page': True}, {'page_size': 101}, {'sort': 'unsupported'}, {'query': []}):
            self.assertFalse(self.request('list_records', **params)['ok'])
        self.assertFalse(self.request('record_detail', attempt_id='unknown')['ok'])

    def test_local_midnight_range_and_calendar_use_same_started_fallback(self):
        # 用本机午夜换算 UTC，避免测试依赖运行机所在时区。
        midnight = datetime(2026, 9, 1).astimezone().astimezone(timezone.utc)
        with self.db:
            self.attempt('aug', moment=(midnight - timedelta(seconds=1)).isoformat(),
                         updated=(midnight + timedelta(days=2)).isoformat())
            self.attempt('sep', moment=midnight.isoformat(), started='旧数据中的无效日期')
        records = self.result('list_records', date_from='2026-09-01', date_to='2026-09-01')
        self.assertEqual([r['id'] for r in records['records']], ['sep'])
        days = self.result('record_days', date_from='2026-08-28', date_to='2026-09-05')
        self.assertEqual(days['days'], ['2026-08-31', '2026-09-01'])
        self.assertFalse(self.request('record_days', date_from='2025-01-01', date_to='2026-01-01')['ok'])
        self.assertFalse(self.request('list_records', date_from='2026-10-02', date_to='2026-10-01')['ok'])

    def test_workbench_scope_counts_attached_queue_and_legacy_snapshot(self):
        old = self.store.create_run({'channels': ['boss'], 'max_contacts': 1}, '测试')
        with self.db:
            self.attempt('owned', status='needs_user')
            self.attempt('attached', old)
            self.attempt('unrelated', old)
            self.db.execute("INSERT INTO run_attempts VALUES (?,'attached','2026-09-10')", (self.run,))
            self.db.execute("INSERT INTO attention VALUES ('done','owned','原因',1,'2026-09-10',0)")
            self.db.execute("INSERT INTO attention VALUES ('todo','unrelated','另一个待办',0,'2026-09-10',0)")
            self.db.execute("INSERT INTO actions VALUES ('a',?,'owned','submit','','{}','key','succeeded',NULL,"
                            "'','2026-09-01','2026-09-05')", (self.run,))
        with patch.object(self.store, 'snapshot', side_effect=AssertionError('桌面轮询不得读取全库快照')):
            data = self.result('snapshot', view='running', run_id=self.run)
            self.assertEqual([r['id'] for r in data['runs']], [self.run])
            self.assertEqual({r['id'] for r in data['attempts']}, {'owned', 'attached'})
            self.assertEqual([r['id'] for r in data['attention']], ['done'])
            self.assertEqual(data['runs'][0]['confirmed_counts'], {'greet': 0, 'submit': 1})
            self.assertEqual(data['runs'][0]['updated_at'], '2026-09-05')
            self.assertEqual(data['pending_count'], 1)
            shell = self.result('snapshot', view='shell')
            self.assertEqual((shell['active_run_count'], shell['runs'], shell['attempts']), (2, [], []))
            self.assertEqual(self.result('snapshot', view='inbox')['attempts'][0]['id'], 'unrelated')
            self.assertNotIn('profile_path', data['runs'][0]['config'])
        self.assertEqual(len(self.result('snapshot')['attempts']), 3)

    def test_home_and_draft_use_recent_real_runs_and_global_aggregates(self):
        active = [self.store.create_run({'channels': ['boss'], 'max_contacts': 1}, '测试') for _ in range(4)]
        sim = self.store.create_run({'channels': ['boss'], 'max_contacts': 1, 'simulation': True}, '演示')
        with self.db:
            self.db.execute("UPDATE runs SET status='completed' WHERE id=?", (self.run,))
            self.attempt('old_result')
            self.attempt('sim_result', sim, moment='2026-11-12T12:00:00Z')
            for run in (sim, active[0]):
                self.db.execute("INSERT INTO actions VALUES (?,?,NULL,'greet','','{}','','succeeded',NULL,"
                                "'','2026-11-12','2026-11-12')", (run, run))
            self.db.execute("INSERT INTO attention VALUES ('i','old_result','interview',0,'2026-01-01',0)")
            self.db.execute("INSERT INTO attention VALUES ('r','old_result','岗位精筛待人工查看：甲公司',0,'2026-01-01',0)")
            self.db.execute("INSERT INTO attention VALUES ('o','old_result','申请需要核查',0,'2026-01-01',0)")
            self.db.execute("INSERT INTO attention VALUES ('resolved','old_result','发现面试邀约',1,'2026-01-01',0)")
        home = self.result('snapshot', view='home', week_start='2026-11-09T00:00:00+08:00')
        self.assertEqual((home['active_run_count'], home['active_real_count'], home['weekly_greet_count']), (5, 4, 1))
        self.assertEqual([row['id'] for row in home['attempts']], ['old_result'])
        self.assertEqual(len(home['runs']), 3)
        self.assertEqual(home['attention_category_counts'], {'interview': 1, 'review': 1, 'other': 1})
        inbox = self.result('snapshot', view='inbox')
        self.assertEqual({row['id']: row['category'] for row in inbox['attention']},
                         {'i': 'interview', 'r': 'review', 'o': 'other'})
        self.assertEqual(self.result('snapshot', view='boss')['runs'][0]['id'], active[-1])
        self.assertEqual(self.result('snapshot', view='companies')['runs'][0]['id'], self.run)
        self.assertEqual(len(self.result('snapshot', view='tasks')['runs']), 6)

    def test_inbox_includes_only_relevant_reply_targets_and_notes(self):
        with self.db:
            self.attempt('relevant')
            self.attempt('irrelevant')
            self.db.execute("INSERT INTO conversations VALUES ('chat','human','','msg','2026-11-01')")
            self.db.execute("INSERT INTO messages VALUES ('chat','msg','对方消息','2026-11-01')")
            self.db.execute("INSERT INTO attention VALUES ('todo','chat','面试邀约',0,'2026-11-01',0)")
            self.db.execute("INSERT INTO attention_notes VALUES ('note','todo','msg','已核对','2026-11-01')")
        folder = self.store.root/'boss-watch'
        folder.mkdir()
        (folder/(self.run+'.json')).write_text(json.dumps({'conversations': {
            'chat': {'attempt_id': 'relevant', 'text': '对方消息', 'message_key': 'msg'},
            'other': {'attempt_id': 'irrelevant', 'text': '其他消息', 'message_key': 'other'},
        }}), encoding='utf-8')
        data = self.result('snapshot', view='inbox')
        self.assertEqual([r['id'] for r in data['runs']], [self.run])
        self.assertEqual([r['id'] for r in data['attempts']], ['relevant'])
        self.assertEqual(data['messages'][0]['text'], '对方消息')
        self.assertEqual(data['notes'][0]['text'], '已核对')
        self.assertEqual([r['conversation'] for r in data['reply_targets']], ['chat'])
