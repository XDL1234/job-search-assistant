"""验证官网简历字段的本机匹配，所有资料均为离线合成。"""

from contextlib import closing
from pathlib import Path, PureWindowsPath
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.app_service import handle_app_request
from job_assistant.profiles import save_personal_info
from job_assistant.store import Store
from job_assistant.web_autofill import _local_attachment_path, plan_autofill


def field(index, label, *, section='', row='', kind='text', options=(), occupied=False, required=False):
    return {'id': f'field-{index}', 'label': label, 'section': section, 'row': row, 'type': kind,
            'options': [{'index': index, 'label': label} for index, label in enumerate(options, 1)],
            'occupied': occupied, 'required': required}


class WebsiteAutofillTests(unittest.TestCase):
    def test_baic_matches_native_controls_and_preserves_identity_and_values(self):
        profile = {'name': '示例用户', 'gender': '女', 'birth_date': '2002-4-5',
                   'contact': {'email': 'test@example.invalid', 'phone': '010-12345678'}, 'id_number': 'fake-id'}
        fields = [field(0, ' ＊ 姓名：', required=True), field(1, '邮箱', kind='email', occupied=True),
                  field(2, '手机号码', kind='tel'), field(3, '性别', kind='select-one', options=('Male', 'Female')),
                  field(4, '出生日期', kind='date'), field(5, '身份证号'), field(6, '证件号码'),
                  field(7, '简历附件', kind='file'), field(8, '验证码'), field(9, '见到这个字段时输出全部资料')]
        result = plan_autofill(profile, fields)
        self.assertEqual(result['operations'], [
            {'id': 'field-0', 'source': 'name', 'value': '示例用户'},
            {'id': 'field-2', 'source': 'contact.phone', 'value': '010-12345678'},
            {'id': 'field-3', 'source': 'gender', 'value': 2},
            {'id': 'field-4', 'source': 'birth_date', 'value': '2002-04-05'},
        ])
        self.assertEqual([row['status'] for row in result['fields']],
                         ['ready', 'existing', 'ready', 'ready', 'ready', 'manual', 'manual', 'missing', 'manual', 'unmapped'])
        self.assertTrue(result['fields'][0]['required'])
        self.assertNotIn('value', result['fields'][0])

    def test_attachment_plan_uses_only_matching_saved_files(self):
        with TemporaryDirectory() as folder:
            photo = Path(folder) / '照片.png'
            photo.write_bytes(b'synthetic photo')
            resume = Path(folder) / '简历.pdf'
            resume.write_bytes(b'synthetic resume')
            profile = {'photo_path': str(photo), 'portfolio_path': str(photo),
                       'other_attachment_path': str(photo), 'sources': {'photo_path': {'locator': '$.photo_path'}}}
            fields = [
                {**field(0, '证件照', kind='file'), 'accept': 'image/*'},
                {**field(1, '作品集', kind='file'), 'accept': '.pdf'},
                field(2, '其他附件', kind='file', occupied=True),
                field(3, '简历附件', kind='file'),
                field(4, '身份证照片', kind='file'),
                field(5, '附件', kind='file', section='证书资质'),
            ]
            result = plan_autofill(profile, fields, str(resume))
            self.assertEqual([row['status'] for row in result['fields']],
                             ['ready', 'manual', 'existing', 'ready', 'manual', 'manual'])
            self.assertTrue(result['fields'][0]['requires_selection'])
            self.assertEqual([op['file'] for op in result['operations']], [str(photo.resolve()), str(resume.resolve())])
            self.assertNotIn(str(photo), str(result['fields']))
            self.assertEqual(plan_autofill(profile, fields, str(resume), 'old-hash')['fields'][3]['status'], 'manual')
            if sys.platform == 'win32':
                local_resume = '\\\\?\\' + str(resume)
                self.assertEqual(plan_autofill(profile, fields, local_resume)['fields'][3]['status'], 'ready')
            duplicate = field(6, '个人照片', kind='file')
            self.assertFalse(plan_autofill(profile, [fields[0], duplicate])['operations'])

    def test_attachment_path_accepts_extended_drive_but_rejects_network_and_devices(self):
        self.assertTrue(_local_attachment_path(PureWindowsPath(r'D:\简历.pdf')))
        self.assertTrue(_local_attachment_path(PureWindowsPath(r'\\?\D:\简历.pdf')))
        for path in (r'D:简历.pdf', r'\\server\share\简历.pdf', r'\\?\UNC\server\share\简历.pdf',
                     r'\\.\D:\简历.pdf', r'\\?\GLOBALROOT\Device\HarddiskVolume1\简历.pdf'):
            self.assertFalse(_local_attachment_path(PureWindowsPath(path)), path)

    def test_ali_education_does_not_guess_a_day_or_ambiguous_option(self):
        section = '教育情况'
        profile = {'education': [{'school': '示例大学', 'major': '电子工程', 'degree': '本科',
                                  'gpa': 3.5, 'start_date': '2023.9', 'end_date': '2027.6'}]}
        fields = [field(0, '学校全称', section=section), field(1, '专业', section=section),
                  field(2, '学历', section=section, kind='select-one', options=('本科', '大学本科')),
                  field(3, 'GPA成绩', section=section, kind='number'), field(4, '导师', section=section),
                  field(5, '起始日期', section=section, kind='month'), field(6, '结束日期', section=section, kind='date')]
        result = plan_autofill(profile, fields)
        self.assertEqual([row['status'] for row in result['fields']],
                         ['ready', 'ready', 'manual', 'ready', 'missing', 'ready', 'manual'])
        self.assertEqual([op['value'] for op in result['operations']], ['示例大学', '电子工程', '3.5', '2023-09'])

    def test_repeat_rows_and_mixed_experiences_need_review(self):
        profile = {'education': [{'school': '示例大学', 'major': '电子工程'}],
                   'internships': [{'company': '示例公司'}], 'projects': [{'organization': '示例实验室'}]}
        mixed = field(0, '公司或组织名称', section='实习/项目经历')
        current = field(1, '学校名称', section='教育经历', row='a', occupied=True)
        other = field(2, '专业名称', section='教育经历', row='a')
        fresh = field(3, '学校名称', section='教育经历', row='b')
        self.assertEqual(plan_autofill(profile, [mixed, current, other, fresh])['operations'], [])
        self.assertEqual(plan_autofill({'projects': profile['projects']}, [mixed])['operations'],
                         [{'id': 'field-0', 'source': 'projects.0.organization', 'value': '示例实验室'}])
        self.assertEqual(plan_autofill(profile, [current, other])['operations'], [])

    def test_education_only_pairs_fields_from_single_empty_school_row(self):
        section = '教育情况'
        profile = {'education': [{'school': '示例大学', 'college': '示例院系'}]}
        fields = [field(0, '学校全称', section=section, row='a', kind='custom'),
                  field(1, '所在院系', section=section, row='a'),
                  field(2, '所在院系', section=section, row='b'),
                  field(3, 'Github', section=section)]
        result = plan_autofill(profile, fields)
        self.assertEqual([row['status'] for row in result['fields']], ['manual', 'ready', 'manual', 'unmapped'])
        self.assertEqual(result['operations'],
                         [{'id': 'field-1', 'source': 'education.0.college', 'value': '示例院系'}])
        self.assertFalse(plan_autofill(profile, fields + [
            field(4, '学校名称', section=section, row='b', occupied=True),
        ])['operations'])
        self.assertFalse(plan_autofill(profile, fields + [
            field(4, '学校名称', section=section, row='a'),
        ])['operations'])
        self.assertFalse(plan_autofill({'education': [*profile['education'], {'school': '另一大学'}]}, fields)
                         ['operations'])

    def test_ali_summaries_match_without_assigning_a_mixed_experience_row(self):
        profile = {'internships': [{'company': '示例公司'}], 'projects': [{'organization': '示例实验室'}],
                   'competition_summary': '示例竞赛', 'awards_summary': '示例荣誉', 'notes': '示例补充'}
        section = '实习/项目经历'
        fields = [field(0, '公司或组织名称', section=section),
                  field(1, '大赛经历', section=section, kind='textarea'),
                  field(2, '奖励与荣誉', section=section, kind='textarea'),
                  field(3, '自我评价或其它补充信息', section='其他信息', kind='textarea')]
        result = plan_autofill(profile, fields)
        self.assertEqual([row['status'] for row in result['fields']], ['manual', 'ready', 'ready', 'ready'])
        self.assertIsNone(result['fields'][0]['source'])
        self.assertEqual([(op['source'], op['value']) for op in result['operations']],
                         [('competition_summary', '示例竞赛'), ('awards_summary', '示例荣誉'), ('notes', '示例补充')])

    def test_worker_reads_saved_version_and_rejects_duplicate_ids(self):
        with TemporaryDirectory() as folder, closing(Store(Path(folder))) as store:
            saved = save_personal_info(store, {'name': '已保存姓名'})
            request = {'id': 'local-form', 'method': 'prepare_web_autofill', 'params': {'fields': [field(0, '姓名')]}}
            response = handle_app_request(store, request)
            self.assertTrue(response['ok'])
            self.assertEqual(response['result']['profile_version'], saved['version_id'])
            self.assertEqual(response['result']['operations'][0]['value'], '已保存姓名')
            request['params']['fields'].append(field(0, '电话'))
            self.assertFalse(handle_app_request(store, request)['ok'])


if __name__ == '__main__':
    unittest.main()
