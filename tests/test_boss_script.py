"""快速模式的纯规则选择，不依赖模型，也不发送网络请求。"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))


class BossScriptTests(unittest.TestCase):
    def test_candidates_keep_platform_order_and_skip_contacted(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'old','job':'嵌入式实习生','company':'A','contacted':True},
               {'key':'new','job':'嵌入式软件实习','company':'B','contacted':False},
               {'key':'next','job':'MCU实习','company':'C','contacted':False}]
        config={'employment_type':'internship','target_roles':['嵌入式','MCU'],'boss_mode':'filtered'}
        self.assertEqual(choose_card(cards,config)['key'],'new')
        self.assertEqual(choose_card(cards,config,{'new'})['key'],'next')

    def test_wrong_employment_and_unknown_cards_are_not_sent(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'full','job':'嵌入式高级工程师','company':'A','contacted':False},
               {'key':'missing','job':'嵌入式实习','company':'','contacted':False}]
        self.assertIsNone(choose_card(cards,{'employment_type':'internship','target_roles':['嵌入式']}))

    def test_interview_and_login_pages_stop_script(self):
        from job_assistant.boss_script import blocking_page
        for text in ['请完成安全验证','扫码登录BOSS直聘','面试邀请，周五有空吗']:
            self.assertIsNotNone(blocking_page(text))
        self.assertIsNone(blocking_page('推荐职位 嵌入式软件实习生 立即沟通'))

    def test_full_time_and_campus_do_not_take_internships(self):
        from job_assistant.boss_script import choose_card
        cards=[{'key':'intern','job':'MCU工程师','tags':'实习','company':'A','contacted':False},
               {'key':'full','job':'MCU工程师','tags':'社招','company':'B','contacted':False},
               {'key':'campus','job':'MCU工程师','tags':'2028届 校招','company':'C','contacted':False}]
        self.assertEqual(choose_card(cards,{'employment_type':'full_time','target_roles':['MCU']})['key'],'full')
        self.assertEqual(choose_card(cards,{'employment_type':'campus','target_roles':['MCU']})['key'],'campus')

if __name__=='__main__':unittest.main()
