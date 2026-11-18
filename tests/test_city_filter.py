"""城市预筛仅排明确异城；范围配置在读材料前验证。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.city_filter import city_match
from job_assistant.cli import prepare_config


class CityFilterTests(unittest.TestCase):
    def test_empty_scope_preserves_all_existing_candidates(self):
        for city in (None, '', '深圳', '广州', '全国', '远程'):
            self.assertIs(city_match(city, []), True)

    def test_city_and_city_district_forms_match_without_substring_matches(self):
        for city in ('深圳', '深圳市', '深圳·南山区', '深圳市南山区', '广东省深圳市·南山区'):
            self.assertIs(city_match(city, ['深圳']), True, city)
        for city in ('广州', '广州市·番禺区', '北京·海淀区', '新深圳'):
            self.assertIs(city_match(city, ['深圳']), False, city)

    def test_unknown_or_ambiguous_locations_are_not_rejected_as_other_cities(self):
        for city in (None, '', '全国', '多地', '远程', '深圳·远程', '深圳/广州', '深圳或广州',
                     '南山区', '广东', '广东省', '未知', '地点待确认', 'remote'):
            self.assertIsNone(city_match(city, ['深圳']), city)

    def test_multiple_cities_and_optional_city_suffix(self):
        self.assertTrue(city_match('广州·番禺区', ['深圳市', ' 广州市 ']))
        self.assertTrue(city_match('深圳市', ['深圳市', '广州']))
        self.assertFalse(city_match('北京·海淀区', ['深圳', '广州']))

    def test_config_requires_a_bounded_array_of_nonempty_strings(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder)/'profile.json'
            profile.write_text(json.dumps({'skills':['MCU']}), encoding='utf-8')
            base = {'channels':['boss'], 'boss_mode':'screened', 'target_roles':['MCU'], 'max_contacts':1,
                    'resume_path':str(profile), 'profile_path':str(profile)}
            for cities in ('深圳', None, True, [''], ['   '], [1], ['深'*41], ['深圳']*21):
                with self.subTest(cities=cities), self.assertRaisesRegex(ValueError, 'cities'):
                    prepare_config({**base, 'cities':cities})
            self.assertEqual(prepare_config({**base, 'cities':[' 深圳 ', '广州市']})['cities'], ['深圳', '广州市'])
            self.assertEqual(prepare_config({**base, 'cities':[]})['cities'], [])
            self.assertNotIn('cities', prepare_config(base))


if __name__ == '__main__':
    unittest.main()
