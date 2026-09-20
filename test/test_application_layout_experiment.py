"""真实OCR实验验证，不代表生产整页或9.16原件验收。"""
import os
from pathlib import Path
import time
import unicodedata
import unittest

import fitz

from agent.agent_backend.utils.parser.pdf_page_extractor import ocr_region, recovery_layout_blocks
from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient


class ApplicationLayoutExperimentTest(unittest.TestCase):
    def test_chained_paragraphs_do_not_form_nearly_full_page_recovery(self):
        with fitz.open() as document:
            page=document.new_page(width=300,height=500)
            for y in range(30,471,20):
                page.insert_text((30,y),'Readable paragraph line',fontsize=10)
            pix=page.get_pixmap(matrix=fitz.Matrix(2.5,2.5),alpha=False)
            blocks=recovery_layout_blocks(page,page.rect,pix,180,time.monotonic()+20)
            self.assertGreater(len(blocks),1)
            self.assertLessEqual(len(blocks),4)
            self.assertLess(max(fitz.Rect(block['bbox']).height for block in blocks),.65*page.rect.height)
            for raw in page.get_text('words'):
                self.assertTrue(any(fitz.Rect(block['bbox']).contains(fitz.Rect(raw[:4])) for block in blocks))

    @unittest.skipUnless(os.getenv('T2_OCR_URL'), '需要显式本地OCR地址')
    def test_automatic_layout_preserves_two_readable_lines(self):
        source = Path(__file__).resolve().parents[1]/'task/change_review/申请表模板.pdf'
        if not source.exists():
            self.skipTest('本地样本缺失')
        original = source.read_bytes()
        with fitz.open(source) as document:
            page = document[0]
            deadline = time.monotonic()+120
            pix = page.get_pixmap(matrix=fitz.Matrix(2.5,2.5),alpha=False)
            blocks = recovery_layout_blocks(page,page.rect,pix,180,deadline)
            # 布局来自像素，独立预期来自原页，不以OCR输出决定选择范围。
            self.assertEqual(len(blocks), 3)
            self.assertEqual(blocks[1]['mode'], 6)
            # 顶部条码是位图，不应令下部原生字形也禁止重新渲染。
            self.assertEqual([block['dpi'] for block in blocks],[180,300,300])
            region = fitz.Rect(blocks[1]['bbox'])
            words, _, _ = ocr_region(document[0], region,
                OCRServiceClient(os.environ['T2_OCR_URL']), 'chi_sim+eng', 300, 6,
                original=True, deadline=deadline)
        self.assertEqual(unicodedata.normalize('NFKC',''.join(w['text'] for w in words)),
                         '法定代表人(签名):(加盖公章处)')
        # 光栅像素取整允许一像素；不允许词框落到其他区域。
        bounds = region + (-.24, -.24, .24, .24)
        self.assertTrue(words)
        self.assertTrue(all(bounds.contains(fitz.Rect(w['bbox'])) for w in words))
        self.assertEqual(source.read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
