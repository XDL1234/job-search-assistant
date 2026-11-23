"""验证扫描件识别和 OCR 行顺序，测试资料使用合成文字。"""

from importlib.util import find_spec
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'apps/worker/scripts'))
from job_assistant.resume_ocr import _text_lines, ocr_pdf_pages


class ResumeOcrTests(unittest.TestCase):
    def test_preserves_line_order_and_wide_column_gaps(self):
        boxes = [
            [(80, 0), (110, 0), (110, 10), (80, 10)],
            [(12, 24), (55, 24), (55, 34), (12, 34)],
            [(10, 1), (55, 1), (55, 11), (10, 11)],
        ]
        self.assertEqual(_text_lines(boxes, ['嵌入式实习生', '工作内容', '示例公司']),
                         ['示例公司  嵌入式实习生', '工作内容'])
        self.assertEqual(ocr_pdf_pages('unused.pdf', []), {})
        with self.assertRaisesRegex(ValueError, '页码'):
            ocr_pdf_pages('unused.pdf', [0])

    @unittest.skipUnless(all(find_spec(module) for module in ('rapidocr', 'onnxruntime', 'pypdfium2', 'PIL')),
                         '需要已安装的离线 OCR 组件')
    def test_scanned_pdf_recognizes_mixed_chinese_and_english_offline(self):
        from PIL import Image, ImageDraw, ImageFont
        from pypdf import PdfReader

        font_path = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'Fonts/msyh.ttc'
        if not font_path.is_file():
            self.skipTest('需要合成扫描页的中文字体')
        font = ImageFont.truetype(str(font_path), 49)
        with tempfile.TemporaryDirectory() as temporary, Image.new('RGB', (1250, 900), 'white') as image:
            path = Path(temporary) / '扫描简历 示例.pdf'
            draw = ImageDraw.Draw(image)
            for y, line in enumerate(('项目经历', '项目名称：嵌入式 STM32', '邮箱：scan@example.com')):
                draw.text((80, 80 + y * 90), line, fill='black', font=font)
            image.save(path, dpi=(150, 150))
            self.assertFalse(PdfReader(path).pages[0].extract_text().strip())
            with patch('requests.sessions.Session.request', side_effect=AssertionError('请求了网络')):
                pages = ocr_pdf_pages(path, [1, 1, 2])
            self.assertEqual(set(pages), {1})
            recognized = '\n'.join(pages[1]).replace(' ', '')
            self.assertIn('项目经历', recognized)
            self.assertIn('STM32', recognized)
            self.assertIn('scan@example.com', recognized)


if __name__ == '__main__':
    unittest.main()
