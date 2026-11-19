"""从合成简历原文核对多种章节和经历布局。"""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/job-search-assistant/scripts'))
from job_assistant.resume_parser import parse_resume


class ResumeParserTests(unittest.TestCase):
    def parse(self, text):
        return parse_resume([{'text': line, 'locator': f'段落 {index}'}
                             for index, line in enumerate(text.strip().splitlines(), 1)], '示例简历.docx')

    def test_chinese_resume_columns_and_continuation_lines(self):
        profile, sources = self.parse('''
示例简历
教育经历
2024.09 – 2028.06      示例大学      自动化     本科
核心课程：数字电路、模拟电子技术
实习经历
示例电子有限公司      嵌入式软件实习生
2025.01– 2025.03
1. 实现设备监控模块，编写调试工具。
2. 维护版本测试报告。
项目经历
智能巡检系统
2025.04 – 2025.06
项目描述：实现巡检路线配置。
项目职责：编写控制程序。
1. 实现运行状态采集
便携温湿度记录仪
2025.07至今
项目描述：设计数据记录流程。
专业技能
设计 GPIO 和串口驱动
''')
        self.assertEqual(profile['education'], [{'school': '示例大学', 'degree': '本科', 'major': '自动化',
                                                'start_date': '2024-09', 'end_date': '2028-06'}])
        self.assertEqual(profile['highest_degree'], '本科')
        self.assertEqual(profile['internships'], [{'company': '示例电子有限公司', 'role': '嵌入式软件实习生',
                                                  'start_date': '2025-01', 'end_date': '2025-03',
                                                  'description': '1. 实现设备监控模块，编写调试工具。\n2. 维护版本测试报告。'}])
        self.assertEqual(profile['projects'], [
            {'name': '智能巡检系统', 'start_date': '2025-04', 'end_date': '2025-06',
             'description': '实现巡检路线配置。\n编写控制程序。\n1. 实现运行状态采集'},
            {'name': '便携温湿度记录仪', 'start_date': '2025-07', 'current': True, 'description': '设计数据记录流程。'},
        ])
        self.assertIn('段落 11', sources['projects']['locator'])
        self.assertIn('项目职责：编写控制程序。', sources['projects']['original'])
        self.assertNotIn('GPIO', sources['projects']['original'])

    def test_labels_mixed_experience_and_available_awards_languages(self):
        profile, _ = self.parse('''
【工作经历】
2024年3月到2024年6月 | 样例实验室 | 开发实习生
实习职责：整理测试用例。
公司名称：示例研究中心 | 职位名称：设备开发工程师
入职时间：2024-07
离职时间：至今
职责：负责设备开发。
项目经验：项目一：状态采集
项目角色：开发负责人
项目二：显示面板
项目描述：搭建面板原型。
获奖情况
2024年10月 三等奖
获奖级别：市级
语言能力
英语：CET-6 510 分
语言类型：法语
掌握程度：初级
''')
        self.assertEqual(profile['internships'][0]['company'], '样例实验室')
        self.assertEqual(profile['work_experience'], [{'company': '示例研究中心', 'role': '设备开发工程师',
                                                       'start_date': '2024-07', 'current': True,
                                                       'description': '负责设备开发。'}])
        self.assertEqual(profile['projects'], [{'name': '状态采集', 'role': '开发负责人'},
                                              {'name': '显示面板', 'description': '搭建面板原型。'}])
        self.assertEqual(profile['awards'], [{'name': '三等奖', 'date': '2024-10', 'level': '市级'}])
        self.assertEqual(profile['languages'], [{'language': '英语', 'certificate': 'CET-6 510 分'},
                                               {'language': '法语', 'proficiency': '初级'}])

    def test_ignores_missing_sections_values_and_unverified_name(self):
        profile, sources = self.parse('''
基本信息
项目组：硬件验证
实习经历
暂无相关实习经历
工作经历
暂无
项目经历
无项目经验。
专业技能
熟悉多种调试工具。
''')
        self.assertEqual(profile, {})
        self.assertEqual(sources, {})

    def test_pdf_style_single_space_before_dates_preserves_title_words(self):
        profile, _ = self.parse('''
实习经验
样例公司    测试实习生 2024年9月1日—2025年1月3日
使用设备验证稳定性。
项目经验
STM32 采集与诊断工具 开发 2025-01 - 2025-03
1. 编写基础驱动
便携显示终端 2025.04到目前
1. 完成线路布置。
''')
        self.assertEqual(profile['internships'][0]['start_date'], '2024-09-01')
        self.assertEqual(profile['internships'][0]['end_date'], '2025-01-03')
        self.assertEqual(profile['internships'][0]['role'], '测试实习生')
        self.assertEqual(profile['projects'][0]['name'], 'STM32 采集与诊断工具 开发')
        self.assertEqual(profile['projects'][1]['name'], '便携显示终端')
        self.assertTrue(profile['projects'][1]['current'])
        self.assertNotIn('end_date', profile['projects'][1])

    def test_no_dates_and_multiline_pdf_page_blocks(self):
        profile, _ = parse_resume([
            {'text': '工作经历\n样例公司    开发工程师\n维护采集模块。\n支持控制设备。', 'locator': '第 1 页'},
            {'text': '另一家样例公司    测试工程师\n验证线路质量。\n项目经历\n项目一：示例装置\n项目二：示例程序',
             'locator': '第 2 页'},
        ], '示例简历.pdf')
        self.assertEqual(profile['work_experience'], [
            {'company': '样例公司', 'role': '开发工程师', 'description': '维护采集模块。\n支持控制设备。'},
            {'company': '另一家样例公司', 'role': '测试工程师', 'description': '验证线路质量。'},
        ])
        self.assertEqual(profile['projects'], [{'name': '示例装置'}, {'name': '示例程序'}])


if __name__ == '__main__':
    unittest.main()
