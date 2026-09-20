"""原图字高驱动的局部输入尺寸、像素相位和坐标回映射。"""
import os
import time
import unittest
from unittest.mock import patch
import fitz
from agent.agent_backend.utils.parser import pdf_page_extractor as parser
from agent.agent_backend.utils.parser.pdf_scan_evidence import recovery_input_size


class InputSizeTests(unittest.TestCase):
    def test_partial_primary_does_not_hide_second_image_line(self):
        from agent.agent_backend.utils.parser.pdf_scan_evidence import recovery_segmentation
        words=[{'text':'Known','bbox':[20,50,80,60]}]
        self.assertEqual(recovery_segmentation(words,[],[{'bbox':[20,20,80,30]}, {'bbox':[20,50,80,60]}]),(6,'image_lines'))

    def test_small_mixed_and_absent_lines_keep_input(self):
        def line(h):return {'bbox':[0,0,100,h/3],'component_bboxes':[[0,0,10,h/3]]}
        for support in ([],[line(20)],[line(12),line(60)],[line(30),line(60)]):
            self.assertIsNone(recovery_input_size((0,0,421,355),support,216))
        self.assertEqual(recovery_input_size((0,0,421,355),[line(60),line(60)],216),(211,178))

    def test_odd_window_pixels_and_numeric_boxes_map_back(self):
        import cv2
        import numpy as np
        with fitz.open() as doc:
            page=doc.new_page(width=200,height=140)
            page.insert_text((30,60),'12.5 mg',fontsize=22)
            crop=fitz.Rect(11,11,151.333333,129.333333)
            pix=page.get_pixmap(matrix=fitz.Matrix(3,3),clip=crop)
            left,top=pix.x%2,pix.y%2
            size=((pix.width+left+1)//2,(pix.height+top+1)//2)
            class Client:
                def image_to_data(self,data,**kwargs):
                    actual=cv2.imdecode(np.frombuffer(data,np.uint8),cv2.IMREAD_COLOR)
                    raw=np.frombuffer(pix.samples,np.uint8).reshape(pix.height,pix.width,3)
                    expected=cv2.resize(cv2.copyMakeBorder(raw,top,(pix.height+top)%2,left,(pix.width+left)%2,
                        cv2.BORDER_CONSTANT,value=(255,255,255)),size,interpolation=cv2.INTER_AREA)
                    np.testing.assert_array_equal(actual,cv2.cvtColor(expected,cv2.COLOR_RGB2BGR))
                    return {'text':['12.5'],'conf':[99],'left':[10],'top':[20],'width':[30],'height':[15],
                        'numeric_verification':[{'word_indices':[0],'bbox_pixel':[10,20,40,35],'status':'verified'}]}
            words,_,_=parser.ocr_region(page,crop,Client(),'eng',216,7,original=True,input_size=size)
            expected=[(pix.x-left+20)/3,(pix.y-top+40)/3,(pix.x-left+80)/3,(pix.y-top+70)/3]
            self.assertEqual(words[0]['bbox'],expected)
            self.assertEqual(words[0]['numeric_verification']['bbox_pdf'],expected)
            self.assertEqual(words[0]['numeric_verification']['status'],'verified')

    @unittest.skipUnless(os.environ.get('DETERMINISTIC_OCR_URL'),'requires local OCR service')
    def test_real_varied_content_and_font_sizes(self):
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient
        for a,b,font in [('生产范围','质量标准','china-s'),('药品名称','不得使用','china-s'),
                         ('Readable sample','Restricted supply','helv')]:
            for size in (12,22,28):
                with self.subTest(font=font,size=size,a=a),fitz.open() as doc:
                    page=doc.new_page(width=450,height=170)
                    page.insert_text((30,55),a,fontsize=size,fontname=font)
                    page.insert_text((30,110),b,fontsize=size,fontname=font)
                    with patch.object(parser,'uncovered_image',return_value=([],None)):
                        words,lines,attempts=parser.recover_ocr_coverage(page,page.rect,
                            OCRServiceClient(os.environ['DETERMINISTIC_OCR_URL']),'chi_sim+eng',216,[],time.monotonic()+120,
                            {'execution_budget_enforced':True,'recovery_cells':[[10,10,440,155]]},
                            quality_lines=[{'bbox':[25,20,405,125]}])
                    if os.environ.get('DETERMINISTIC_OUTPUT'):
                        import json
                        from pathlib import Path
                        out=Path(os.environ['DETERMINISTIC_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
                        name=f'{font}-{size}-'+('scope' if a=='生产范围' else 'name' if a=='药品名称' else 'english')
                        (out/(name+'.json')).write_text(json.dumps(dict(expected=a+b,words=words,lines=lines,attempts=attempts),ensure_ascii=False,indent=2))
                    self.assertEqual(''.join(w['text'] for w in words).replace(' ',''),(a+b).replace(' ',''))
                    self.assertEqual(len(attempts),1)
                    self.assertEqual(attempts[0]['segmentation_psm'],6)
                    self.assertEqual(len(lines),2)


if __name__=='__main__':unittest.main()
