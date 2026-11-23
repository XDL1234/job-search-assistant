"""筛选选项必须经BOSS读回；测试不连招聘平台。"""
from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, call

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.agent_browser import Browser
from job_assistant.boss_filter_dom import (
    FilterControls, apply_filters, platform_scope, same_city, selected_city_code, verify_geography_scope,
)
from job_assistant.job_preferences import normalize_filters


URL = 'https://www.zhipin.com/web/geek/jobs?city=example&jobType=selected'


class FilterVerificationTests(unittest.TestCase):
    def test_city_suffix_can_use_same_verified_platform_city(self):
        browser = Mock()
        browser.verify_target.return_value = {'url': URL}
        browser.evaluate.return_value = {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型'], 'area': ''}
        browser.wait.return_value = browser.evaluate.return_value
        result = apply_filters(browser, {'city': '深圳市'})
        self.assertEqual(result['city'], '深圳')
        browser.filter_input.assert_not_called()
        self.assertFalse(same_city('深圳', '龙岗市'))
        self.assertFalse(same_city('市中区', '市中'))

    def test_province_readback_uses_home_evidence_and_restores_jobs_page(self):
        browser = Mock()
        code = '123456'
        jobs = URL.replace('example', code)
        browser.evaluate.side_effect = [
            {'home_path': '/example-city/'},
            {'ready': True},
            {'selected': True},
            {'found': True, 'city_code': code},
        ]
        browser.wait.return_value = {'ready': True}
        self.assertEqual(FilterControls(browser).city('深圳市', '广东省', {'url': jobs, 'city': '深圳'}), code)
        self.assertEqual(browser.command.call_args_list,
                         [call('open', 'https://www.zhipin.com/example-city/'), call('open', jobs)])
        browser.filter_input.assert_not_called()

    def test_province_check_rejects_unsafe_home_path_and_ambiguous_city_codes(self):
        browser = Mock()
        browser.evaluate.return_value = {'home_path': '//another.example/'}
        with self.assertRaisesRegex(ValueError, '未提供可核验省市'):
            FilterControls(browser).province_city('深圳', '广东', URL)
        browser.command.assert_not_called()
        self.assertIsNone(selected_city_code(URL + '&city=123456'))

    def test_province_check_uses_public_home_when_jobs_page_has_no_city_home_link(self):
        browser = Mock()
        browser.evaluate.side_effect = [
            {'home_path': None}, {'ready': True}, {'selected': True},
            {'found': True, 'city_code': '123456'},
        ]
        browser.wait.return_value = {'ready': True}
        self.assertEqual(FilterControls(browser).province_city('深圳', '广东', URL), '123456')
        self.assertEqual(browser.command.call_args_list,
                         [call('open', 'https://www.zhipin.com/'), call('open', URL)])

    def test_province_does_not_accept_another_city_with_the_same_label(self):
        browser = Mock()
        controls = FilterControls(browser)
        controls.province_city = Mock(return_value='123456')
        wrong = {'url': URL.replace('example', '987654'), 'city': '深圳'}
        browser.evaluate.side_effect = [{'ready': True}, {'found': False},
                                        {'found': True, 'click_selector': 'generated'}]
        browser.wait.side_effect = [{'ready': True}, wrong]
        with self.assertRaisesRegex(ValueError, '无法核验已选城市属于'):
            controls.city('深圳市', '广东', wrong)

    def test_no_invented_metro_reset(self):
        browser = Mock()
        with self.assertRaisesRegex(ValueError, '地铁线路请填写具体线路'):
            apply_filters(browser, {'city': '深圳', 'metro_line': '不限'})
        browser.verify_target.assert_not_called()

    def test_unrestricted_business_area_means_any_area_inside_selected_district(self):
        self.assertEqual(
            normalize_filters({'city': '深圳', 'district': '南山区', 'business_area': '不限'}, 'internship'),
            {'city': '深圳', 'district': '南山区'},
        )

    def test_geography_readback_rejects_the_opposite_filter_mode(self):
        with self.assertRaisesRegex(ValueError, '清除旧地铁条件'):
            verify_geography_scope([['multiSubway', 'other']], {'district': '不限'})
        params = [['multiBusinessDistrict', 'other'], ['multiSubway', 'selected']]
        with self.assertRaisesRegex(ValueError, '地铁范围与请求不一致'):
            verify_geography_scope(params, {'metro_line': '1号线'})

    def test_selected_option_can_be_used_only_after_click_reads_back(self):
        browser = Mock()
        browser.verify_target.return_value = {'url': URL}
        browser.evaluate.side_effect = [
            {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型'], 'area': '工作区域'},
            {'found': True, 'selected': False, 'click_selector': 'generated'},
            {'found': True, 'selected': False},
        ]
        browser.wait.return_value = {'found': True, 'selected': False}
        with self.assertRaisesRegex(ValueError, '无法读回.*实习'):
            apply_filters(browser, {'job_type': '实习'})
        browser.filter_input.assert_called_once_with({'found': True, 'selected': False, 'click_selector': 'generated'})
        self.assertEqual(browser.evaluate.call_args_list.count(call('filter_dom', kind='state')), 1)

    def test_already_selected_control_is_not_toggled_and_actual_scope_is_returned(self):
        browser = Mock()
        browser.verify_target.return_value = {'url': URL}
        browser.evaluate.side_effect = [
            {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(1)'], 'area': '工作区域'},
            {'found': True, 'selected': True},
            {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(1)'], 'area': '工作区域'},
            {'found': True, 'selected': True},
            {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(1)'], 'area': '工作区域'},
        ]
        browser.wait.return_value = {'found': True, 'selected': True}
        browser.wait.side_effect = [{'labels': ['求职类型(1)'], 'area': '工作区域'}]
        actual = apply_filters(browser, {'job_type': '实习'})
        browser.filter_input.assert_not_called()
        browser.command.assert_called_once_with('reload')
        self.assertEqual(actual['filters'], {'job_type': '实习'})
        self.assertEqual(actual['scope'], {'path': '/web/geek/jobs', 'params': [['city', 'example'], ['jobType', 'selected']]})

    def test_geography_requires_city_and_chat_only_navigates_to_public_jobs_page(self):
        browser = Mock()
        with self.assertRaises(ValueError):
            apply_filters(browser, {'district': '南山区'})
        browser.verify_target.assert_not_called()
        browser.verify_target.return_value = {'url': 'https://www.zhipin.com/web/geek/chat'}
        state = {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(1)'], 'area': ''}
        browser.evaluate.side_effect = [state, {'found': True, 'selected': True}, state, {'found': True, 'selected': True}, state]
        browser.wait.return_value = state
        apply_filters(browser, {'job_type': '实习'})
        self.assertEqual(browser.command.call_args_list, [call('open', 'https://www.zhipin.com/web/geek/jobs'), call('reload')])

    def test_retry_reopens_closed_menu_before_verifying_state(self):
        browser, opener = Mock(), Mock()
        browser.evaluate.side_effect = [{'found': True, 'selected': False}, {'found': False}]
        browser.wait.return_value = {'found': True, 'selected': True}
        FilterControls(browser).select('option', opener, group='求职类型', label='全职')
        opener.assert_called_once_with()
        browser.filter_input.assert_called_once()

    def test_trigger_moves_off_stale_hover_before_reopening(self):
        browser = Mock()
        hovered = {'found': True, 'hovered': True, 'hover_selector': 'first'}
        reset = {'found': True, 'hover_selector': 'search'}
        trigger = {'found': True, 'hover_selector': 'second'}
        browser.wait.side_effect = [hovered, reset, trigger]
        FilterControls(browser).trigger('trigger', group='求职类型')
        self.assertEqual(browser.filter_input.call_args_list, [call(reset), call(trigger)])

    def test_final_readback_stops_when_later_page_update_changes_range(self):
        browser = Mock()
        browser.verify_target.return_value = {'url': URL}
        state = {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(1)'], 'area': ''}
        switched = {**state, 'url': URL.replace('example', 'different')}
        browser.wait.return_value = state
        browser.evaluate.side_effect = [state, {'found': True, 'selected': True}, state,
                                        {'found': True, 'selected': True}, switched]
        with self.assertRaisesRegex(ValueError, '最终核对时发生变化'):
            apply_filters(browser, {'job_type': '实习'})
        browser.filter_input.assert_not_called()

    def test_final_readback_refuses_additional_selected_values(self):
        browser = Mock()
        browser.verify_target.return_value = {'url': URL}
        state = {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(2)'], 'area': ''}
        browser.wait.return_value = state
        browser.evaluate.side_effect = [state, {'found': True, 'selected': True}, state,
                                        {'found': True, 'selected': True}, state]
        with self.assertRaisesRegex(ValueError, '筛选项数量与要求不一致'):
            apply_filters(browser, {'job_type': '实习'})

    def test_transient_choice_lost_after_reload_is_not_proved(self):
        browser = Mock()
        browser.verify_target.return_value = {'url': URL}
        state = {'url': URL, 'city': '深圳', 'query': '', 'labels': ['求职类型(1)'], 'area': ''}
        lost = {'found': True, 'selected': False}
        browser.evaluate.side_effect = [state, lost, {'found': True, 'selected': True}, state, lost]
        browser.wait.side_effect = [state, lost]
        with self.assertRaisesRegex(ValueError, '最终复核未选中.*实习'):
            apply_filters(browser, {'job_type': '实习'})
        browser.command.assert_called_once_with('reload')

    def test_native_action_cannot_target_arbitrary_selectors(self):
        browser = object.__new__(Browser)
        browser.command = Mock()
        with self.assertRaises(ValueError):
            browser.filter_input({'click_selector': 'button'})
        with self.assertRaises(ValueError):
            browser.filter_input({'input_selector': 'input'}, '嵌入式')
        browser.command.assert_not_called()
        selector = '[data-job-assistant-action="0123456789abcdef0123456789abcdef"]'
        with self.assertRaises(ValueError):
            browser.filter_input({'hover_selector': selector, 'point': [-1, 50]})
        browser.filter_input({'hover_selector': selector, 'point': [21.5, 50]})
        browser.command.assert_called_once_with('mouse', 'move', '21.5', '50')

    def test_readback_scope_rejects_other_endpoints_and_hosts(self):
        with self.assertRaises(ValueError):
            platform_scope('https://www.zhipin.com/web/geek/chat?city=example')
        with self.assertRaises(ValueError):
            platform_scope('https://fake.zhipin.com/web/geek/jobs')
        self.assertEqual(platform_scope(URL), platform_scope('https://www.zhipin.com/web/geek/jobs?jobType=selected&city=example'))


if __name__ == '__main__':
    unittest.main()
