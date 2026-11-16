"""真实格式提取、材料指纹、版本与个人数据隔离。"""
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'skills/job-search-assistant/scripts'))
from job_assistant.store import Store
from job_assistant.app_service import handle_app_request


class ProfilesTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'data')
        self.addCleanup(self.store.close)

    def call(self,method,**params):
        return handle_app_request(self.store,{'id':'test','method':method,'params':params})

    def test_docx_paragraphs_and_tables_have_sources_without_duplicates(self):
        path=self.root/'含 空格 中文.docx'
        with zipfile.ZipFile(path,'w') as z:
            z.writestr('word/document.xml','''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
            <w:p><w:r><w:t>测试同学</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>邮箱：student@example.com</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
            <w:p><w:r><w:t>求职意向：嵌入式软件实习生</w:t></w:r></w:p></w:body></w:document>''')
        result=self.call('extract_profile',path=str(path))
        self.assertTrue(result['ok'],result)
        value=result['result']
        self.assertEqual(len(value['blocks']),3)
        self.assertEqual(value['profile']['contact']['email'],'student@example.com')
        self.assertEqual(value['profile']['target_role'],'嵌入式软件实习生')
        self.assertIn('段落',value['blocks'][1]['locator'])

    def test_json_roundtrip_versions_are_immutable(self):
        source=self.root/'个人资料.json'
        source.write_text(json.dumps({'name':'测试同学','projects':['STM32'],'contact':{'email':'demo@example.com'}}))
        extraction=self.call('extract_profile',path=str(source))['result']
        for confirmed in (False,True):
            saved=self.call('save_profile',extraction=extraction,profile=extraction['profile'],resume_path=str(source),confirmed=confirmed)
            self.assertEqual(saved['ok'],confirmed,saved)
        first=Path(saved['result']['profile_path'])
        original=first.read_bytes()
        changed={**extraction['profile'],'name':'另一个测试值'}
        self.assertTrue(self.call('save_profile',extraction=extraction,profile=changed,resume_path=str(source),confirmed=True)['ok'])
        self.assertEqual(first.read_bytes(),original)
        self.assertEqual(self.call('get_profile')['result']['profile']['name'],'另一个测试值')

    def test_material_change_rejected_and_current_profile_kept(self):
        source=self.root/'个人资料.json';source.write_text('{"name":"A"}')
        extraction=self.call('extract_profile',path=str(source))['result']
        source.write_text('{"name":"B"}')
        self.assertFalse(self.call('save_profile',extraction=extraction,profile=extraction['profile'],resume_path=str(source),confirmed=True)['ok'])
        self.assertIsNone(self.call('get_profile')['result'])

    def test_blank_pdf_and_old_doc_have_explicit_failures(self):
        from pypdf import PdfWriter
        path=self.root/'扫描.pdf'
        writer=PdfWriter();writer.add_blank_page(width=200,height=200)
        writer.write(path)
        result=self.call('extract_profile',path=str(path))
        self.assertFalse(result['ok'],result)
        self.assertIn('文本',result['error']['message'])
        path=self.root/'旧.doc';path.write_bytes(b'old')
        self.assertFalse(self.call('extract_profile',path=str(path))['ok'])

    def test_pdf_text_source_page_and_corrupted_file(self):
        from pypdf import PdfWriter
        from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject
        writer=PdfWriter();page=writer.add_blank_page(width=400,height=300)
        font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
        page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
        stream=DecodedStreamObject();stream.set_data(b'BT /F1 12 Tf 20 100 Td (Email: pdf@example.com) Tj ET')
        page[NameObject('/Contents')]=writer._add_object(stream)
        path=self.root/'文字.pdf';writer.write(path)
        result=self.call('extract_profile',path=str(path))
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['result']['profile']['contact']['email'],'pdf@example.com')
        self.assertIn('第 1 页',result['result']['blocks'][0]['locator'])
        path.write_bytes(b'not a pdf')
        self.assertFalse(self.call('extract_profile',path=str(path))['ok'])


if __name__=='__main__':unittest.main()
