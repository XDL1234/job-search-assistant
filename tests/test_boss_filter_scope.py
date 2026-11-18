"""本轮授权、平台核验和岗位来源使用同一个筛选范围。"""
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.agent_browser import check_current_source
from job_assistant.app_runs import preview_run, start_run
from job_assistant.boss_filter_scope import check_list_scope, list_scope, prepare_run_filters, read_scope
from job_assistant.profiles import extract_profile, save_profile
from job_assistant.store import Store


class FilterScopeTests(unittest.TestCase):
    def test_list_scope_preserves_platform_filters_but_allows_pagination(self):
        url = 'https://www.zhipin.com/web/geek/jobs?city=2&jobType=3&position=10%2C20&query=MCU'
        proof = {'scope': list_scope(url)}
        check_list_scope(proof, url+'&page=2&ka=search')
        with self.assertRaisesRegex(ValueError, '筛选已变化'):
            check_list_scope(proof, url.replace('jobType=3', 'jobType=4'))
        with self.assertRaises(ValueError):
            check_list_scope(proof, url.replace('/web/geek/jobs', '/web/geek/chat'))

    def test_current_recommendations_require_same_url_scope_and_visible_filters_when_rebound(self):
        from job_assistant.boss_filter_scope import _observe_listing
        with tempfile.TemporaryDirectory() as folder, closing(Store(Path(folder)/'data')) as store:
            url = 'https://www.zhipin.com/web/geek/recommend?city=101280600'
            original = {'url':url, 'cards':[{'key':'synthetic'}], 'filters':['深圳', '实习']}
            with patch('job_assistant.agent_browser.Browser') as browser_type:
                browser = browser_type.return_value
                browser.config = {'pinned_target':'tab-a'}
                browser.verify_target.return_value = {'url': url}
                browser.wait.return_value = original
                proof = _observe_listing(store, 'tab-a', None, None)
                browser.command.assert_not_called()
                browser.config = {'pinned_target':'tab-b'}
                browser.verify_target.return_value = {'url':'https://www.zhipin.com/web/geek/chat'}
                browser.wait.return_value = {**original, 'filters':['青岛', '实习']}
                with self.assertRaisesRegex(ValueError, '无法还原'):
                    _observe_listing(store, 'tab-b', None, proof)
                browser.command.assert_called_once_with('open', url)
                browser.wait.return_value = original
                self.assertEqual(_observe_listing(store, 'tab-b', None, proof)['target'], 'tab-b')

    def test_apply_once_binds_authorized_job_source_and_resume_does_not_reset_page(self):
        with tempfile.TemporaryDirectory() as folder, closing(Store(Path(folder)/'data')) as store:
            source = Path(folder)/'profile.json'
            source.write_text('{"name":"测试用户"}', encoding='utf-8')
            extracted = extract_profile(source)
            save_profile(store, extracted, extracted['profile'], source, True)
            preview = preview_run(store, {'channels': ['boss'], 'employment_type': 'campus',
                 'boss_mode': 'filtered', 'execution_mode': 'script', 'target_roles': ['MCU'],
                 'max_contacts': 1, 'window_title': 'BOSS直聘', 'boss_filters': {'city': '深圳'}})
            config = preview['config']
            run_id = start_run(store, {'ticket': preview['ticket'], 'confirmed': True})['run_id']
            url = 'https://www.zhipin.com/web/geek/jobs?city=2&experience=3&jobType=1'
            def apply_filters(_, values, target, check=None):
                return {'target':target, 'url':url, 'scope':list_scope(url)}
            with patch('job_assistant.boss_filter_scope._apply', side_effect=apply_filters) as apply:
                with self.assertRaisesRegex(ValueError, '暂停'):
                    prepare_run_filters(store, run_id, 'real-tab')
                store.control(run_id, 'running')
                prepare_run_filters(store, run_id, 'real-tab')
                self.assertEqual(apply.call_args.args[1], {'city':'深圳', 'experience':'应届生', 'job_type':'全职'})
                store.control(run_id, 'paused')
                store.control(run_id, 'running')
                prepare_run_filters(store, run_id, 'real-tab')
                self.assertEqual(apply.call_count, 1)
                with patch('job_assistant.boss_filter_scope._observe_listing') as restore:
                    restore.side_effect = ValueError('新标签无法还原本轮筛选')
                    with self.assertRaisesRegex(ValueError, '无法还原'):
                        prepare_run_filters(store, run_id, 'other-tab')
                    self.assertEqual(read_scope(store.root, run_id, config)['target'], 'real-tab')
                    restore.side_effect = None
                    state = store.root/'script-state'/(run_id+'.json')
                    state.parent.mkdir()
                    state.write_text(json.dumps({'phase':'greet','target':'real-tab'}), encoding='utf-8')
                    prepare_run_filters(store, run_id, 'other-tab')
                    self.assertEqual(json.loads(state.read_text(encoding='utf-8')), {'phase':'list','target':'other-tab'})
            self.assertEqual(read_scope(store.root, run_id, config)['scope'], list_scope(url))
            self.assertEqual(read_scope(store.root, run_id, config)['target'], 'other-tab')
            job_key = '/job_detail/test.html'
            source_path = store.root/'agent-browser-state'/(run_id+'.json')
            source_path.write_text(json.dumps({'target_id':'other-tab','source_card':{'key':job_key},
                                               'return_url':url}), encoding='utf-8')
            check_current_source(store.root, run_id, config, job_key)
            source_path.write_text(json.dumps({'target_id':'other-tab','source_card':{'key':job_key},
                                               'return_url':url.replace('city=2', 'city=5')}), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '筛选已变化'):
                check_current_source(store.root, run_id, config, job_key)

    def test_same_tab_restores_only_the_detail_open_interrupted_before_phase_update(self):
        with tempfile.TemporaryDirectory() as folder, closing(Store(Path(folder)/'data')) as store:
            profile = Path(folder)/'profile.json'
            profile.write_text('{"name":"测试用户"}', encoding='utf-8')
            extracted = extract_profile(profile)
            save_profile(store, extracted, extracted['profile'], profile, True)
            url = 'https://www.zhipin.com/web/geek/jobs?city=2'
            target = 'real-tab'
            job = '/job_detail/test.html'
            detail = 'https://www.zhipin.com'+job
            state_dir = store.root/'script-state'; state_dir.mkdir()
            for mode in ('script', 'ai'):
                with self.subTest(mode=mode):
                    preview = preview_run(store, {'channels':['boss'], 'employment_type':'any',
                        'boss_mode':'filtered', 'execution_mode':mode, 'target_roles':['MCU'],
                        'max_contacts':1, 'window_title':'BOSS直聘', 'boss_filters':{'city':'深圳'}})
                    run = start_run(store, {'ticket':preview['ticket'], 'confirmed':True})['run_id']
                    store.control(run, 'running')
                    applied = {'target':target, 'url':url, 'scope':list_scope(url)}
                    with patch('job_assistant.boss_filter_scope._apply', return_value=applied):
                        prepare_run_filters(store, run, target)
                    checkpoint = store.root/'agent-browser-state'/(run+'.json')
                    checkpoint.write_text(json.dumps({'target_id':target, 'source_card':{'key':job},
                        'navigate':detail, 'return_url':url, 'return_filters':['深圳'],
                        'return_position':{'top':0}}), encoding='utf-8')
                    state_path = state_dir/(run+'.json')
                    if mode == 'script':
                        state_path.write_text(json.dumps({'phase':'open', 'card':{'key':job}}), encoding='utf-8')
                    else:
                        with store.conn:
                            state = {'phase':'execute_open', 'approved':[{'card':{'key':job}}]}
                            store.conn.execute('INSERT INTO app_ai_selection VALUES (?,?)', (run, json.dumps(state)))
                    with patch('job_assistant.agent_browser.Browser') as browser_type:
                        browser = browser_type.return_value
                        browser.config = {'pinned_target':target}
                        browser.verify_target.return_value = {'url':detail}
                        browser.wait.return_value = {'url':url, 'filters':['深圳'], 'list_empty':True,
                            'position':{'top':0, 'height':800, 'viewport':400}}
                        browser.capture.side_effect = lambda frame: frame
                        prepare_run_filters(store, run, target)
                        browser.command.assert_called_once_with('back')
                        if mode == 'script':
                            state_path.write_text(json.dumps({'phase':'greet', 'card':{'key':job}}), encoding='utf-8')
                            prepare_run_filters(store, run, target)
                            browser.command.assert_called_once_with('back')
