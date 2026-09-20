import unittest
import os
from unittest.mock import Mock, patch
import fitz
from agent.agent_backend.utils.parser import pdf_page_extractor as parser
from agent.agent_backend.utils.parser.pdf_scan_evidence import embedded_comparisons


def word(text, box, **extra):
    return dict(text=text, bbox=list(box), confidence=95, source='ocr', **extra)


class DeterministicRecoveryTests(unittest.TestCase):
    def test_image_lines_vary_content_size_and_multiline_layout(self):
        from agent.agent_backend.utils.parser.pdf_scan_evidence import image_line_support, recovery_segmentation
        for text,font in [('Changed sample','helv'),('药品生产范围','china-s')]:
            for size in (12,20,28):
                with fitz.open() as doc:
                    page=doc.new_page(width=400,height=200)
                    page.insert_text((30,60),text,fontsize=size,fontname=font)
                    support=image_line_support(page,page.rect,216,float('inf'))
                    self.assertEqual(recovery_segmentation([],[],support)[0],7,(text,size,support))
                    page.insert_text((30,130),text,fontsize=size,fontname=font)
                    support=image_line_support(page,page.rect,216,float('inf'))
                    self.assertEqual(recovery_segmentation([],[],support)[0],6,(text,size,support))

    def test_cross_row_bridge_does_not_merge_two_lines(self):
        old=[word('Top',[10,10,60,20]),word('Bottom',[10,40,60,50])]
        result=embedded_comparisons(old,[word('TopBottom',[10,10,60,50])])
        self.assertEqual(len(result),2)
        self.assertTrue(all(x['status']!='matched' for x in result))

    def test_image_evidence_restores_to_original_coordinates(self):
        from agent.agent_backend.utils.parser.pdf_deskew import restore_coordinates
        p={'page_bbox':[0,0,200,100],'evidence':{'bbox':[10,20,30,40],
            'requested_bbox_pdf':[10,20,30,40],'component_bboxes':[[10,20,12,25]],
            'comparison_cells':[[0,0,100,100]],'pixel_window':[30,60,90,120]}}
        restore_coordinates(p,[[1,0,30],[0,1,60]],3,[0,0,200,100])
        self.assertEqual(p['evidence']['requested_bbox_pdf'],[20,40,40,60])
        self.assertEqual(p['evidence']['component_bboxes'],[[20,40,22,45]])
        self.assertEqual(p['evidence']['pixel_window'],[30,60,90,120])

    def test_unverified_numeric_crop_is_not_adopted(self):
        candidate=word('12.5',[20,40,60,55])
        with patch.object(parser,'uncovered_image',return_value=([[20,40,60,55]],None)),patch.object(
                parser,'ocr_region',return_value=([candidate],[],None)):
            added,_,attempts=parser.recover_ocr_coverage(None,fitz.Rect(0,0,200,100),Mock(),'eng',216,
                [word('Anchor',[20,10,70,25])],float('inf'),{'execution_budget_enforced':True})
        self.assertFalse(added)
        self.assertEqual(attempts[0]['candidates'][0]['text'],'12.5')

    def test_render_window_matches_actual_crop_rotation_userunit(self):
        from agent.agent_backend.utils.parser.pdf_scan_evidence import pixel_crop
        for rotation in (0,90,180,270):
            for unit in (1,2):
                with fitz.open() as doc:
                    page=doc.new_page(width=240,height=160)
                    page.set_cropbox(fitz.Rect(10,10,220,150))
                    doc.xref_set_key(page.xref,'UserUnit',str(unit))
                    page=doc.reload_page(page)
                    page.set_rotation(rotation)
                    crop=fitz.Rect(12.11,15.12,75.24,90.27)
                    canonical,window=pixel_crop(page,crop,216)
                    pix=page.get_pixmap(matrix=fitz.Matrix(3,3),clip=canonical)
                    self.assertEqual(tuple(pix.irect),window)

    def test_valid_engine_order_survives_candidate_copy(self):
        from copy import deepcopy
        from agent.agent_backend.utils.parser.pdf_scan_evidence import ordered_text
        ws=[word('左',[10,21,20,31]),word('右',[21,19,31,31])]
        self.assertEqual(ordered_text(deepcopy(ws),[{'_ordered_words':ws}])[0],'左 右')

    @unittest.skipUnless(os.environ.get('DETERMINISTIC_ORIGINALS'),'requires private original fixtures')
    def test_original_duplicate_crops_use_one_real_request(self):
        import json,time
        from pathlib import Path
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient
        old=json.loads(Path('/tmp/certificate-implementation-final/doc2-pages.json').read_text())[2]
        previous=[a for a in old['ocr_recovery'] if a['status']!='not_attempted' and 'reused_attempt_index' not in a][-2:]
        crops=[fitz.Rect(a['bbox_pdf']) for a in previous]
        class CountingClient(OCRServiceClient):
            calls=0
            def image_to_data(self,*args,**kwargs):
                self.calls+=1
                return super().image_to_data(*args,**kwargs)
        client=CountingClient(os.environ['DETERMINISTIC_OCR_URL'])
        with fitz.open(sorted(Path(os.environ['DETERMINISTIC_ORIGINALS']).glob('*.pdf'))[1]) as doc:
            page=doc[2]
            with patch.object(parser,'uncovered_image',return_value=([list(c) for c in crops],None)),patch.object(
                    parser,'recovery_boxes',return_value=[(c,c) for c in crops]):
                _,_,attempts=parser.recover_ocr_coverage(page,page.rect,client,'chi_sim+eng',216,old['words'],time.monotonic()+120,
                    {'execution_budget_enforced':True,'recovery_cells':[c['bbox_pdf'] for t in old['tables'] for c in t['cells']]})
        self.assertEqual(client.calls,1)
        self.assertEqual(len(attempts),2)
        self.assertEqual(attempts[1]['reused_attempt_index'],0)
        if os.environ.get('DETERMINISTIC_OUTPUT'):
            out=Path(os.environ['DETERMINISTIC_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
            (out/'original-crop-comparison.json').write_text(json.dumps({'old_calls':2,'new_calls':client.calls,
                'mode':'actual_original_crops_real_ocr_controlled_recovery_jobs','attempts':attempts},ensure_ascii=False,indent=2))

    def test_unknown_line_count_defaults_to_block(self):
        from agent.agent_backend.utils.parser.pdf_scan_evidence import recovery_segmentation
        self.assertEqual(recovery_segmentation([],[],[]),(6,'line_count_uncertain'))

    def test_two_lines_are_not_flattened_and_symbols_unchanged(self):
        old=[word('Dose <=2 mg',[0,10,90,20]),word('不得使用',[0,35,90,45])]
        current=[word('Dose <=2 mg',[0,10,90,20]),word('不得使用',[0,35,90,45])]
        e=embedded_comparisons(old,current)
        self.assertEqual(len(e),2)
        self.assertTrue(all(x['status']=='matched' for x in e))
        current[0]['text']='Dose <2 mg'
        self.assertEqual(embedded_comparisons(old,current)[0]['status'],'conflict')

    def test_blank_slash_and_frame_are_not_text_support(self):
        from agent.agent_backend.utils.parser.pdf_scan_evidence import image_line_support
        for shape in ('blank','slash','frame'):
            with fitz.open() as doc:
                page=doc.new_page(width=240,height=100)
                if shape=='slash':page.draw_line((30,60),(50,30))
                if shape=='frame':page.draw_rect((20,20,220,80))
                self.assertEqual(image_line_support(page,page.rect,216,float('inf')),[],shape)
                with patch.object(parser,'uncovered_image',return_value=([[20,20,120,70]],None)),patch.object(
                        parser,'ocr_region',return_value=([word('Fabricated',[25,30,100,50])],[],None)):
                    added,_,attempts=parser.recover_ocr_coverage(page,page.rect,Mock(),'eng',216,[],float('inf'),
                        {'execution_budget_enforced':True,'recovery_cells':[[0,0,240,100]]})
                self.assertFalse(added,shape)
                self.assertTrue(attempts[0]['candidates'])

    def test_adjacent_pixel_windows_not_reused(self):
        with fitz.open() as doc:
            page=doc.new_page(width=200,height=100)
            boxes=[[10,10,50,30],[50,10,90,30]]
            with patch.object(parser,'uncovered_image',return_value=(boxes,None)),patch.object(
                    parser,'recovery_boxes',return_value=[(fitz.Rect(b),fitz.Rect(b)) for b in boxes]),patch.object(
                    parser,'ocr_region',return_value=([],[],None)) as call:
                parser.recover_ocr_coverage(page,page.rect,Mock(),'eng',216,[],float('inf'),{'execution_budget_enforced':True})
            self.assertEqual(call.call_count,2)

    @unittest.skipUnless(os.environ.get('DETERMINISTIC_OCR_URL'),'requires local OCR service')
    def test_real_chinese_multiline_empty_primary(self):
        import time
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient
        with fitz.open() as doc:
            page=doc.new_page(width=260,height=140)
            page.insert_text((30,55),'生产范围',fontsize=22,fontname='china-s')
            page.insert_text((30,100),'质量标准',fontsize=22,fontname='china-s')
            with patch.object(parser,'uncovered_image',return_value=([],None)):
                words,_,attempts=parser.recover_ocr_coverage(page,page.rect,
                    OCRServiceClient(os.environ['DETERMINISTIC_OCR_URL']),'chi_sim+eng',216,[],time.monotonic()+120,
                    {'execution_budget_enforced':True,'recovery_cells':[[10,10,250,130]]},
                    quality_lines=[{'bbox':[25,28,125,107]}])
            if os.environ.get('DETERMINISTIC_OUTPUT'):
                import json
                from pathlib import Path
                out=Path(os.environ['DETERMINISTIC_OUTPUT']);out.mkdir(parents=True,exist_ok=True)
                (out/'chinese-multiline.json').write_text(json.dumps({'expected':'生产范围质量标准',
                    'actual':''.join(w['text'] for w in words),'attempts':attempts},ensure_ascii=False,indent=2))
                doc.save(out/'chinese-multiline.pdf')
            self.assertEqual(''.join(w['text'] for w in words),'生产范围质量标准')
            self.assertEqual(attempts[0]['segmentation_psm'],6)

    @unittest.skipUnless(os.environ.get('DETERMINISTIC_OCR_URL'),'requires local OCR service')
    def test_real_ocr_recovers_empty_primary_into_table_and_text(self):
        import time
        from agent.agent_backend.utils.parser.drug_supplement_pdf_parser import OCRServiceClient
        with fitz.open() as doc:
            page=doc.new_page(width=260,height=110)
            page.insert_text((30,55),'Readable sample',fontsize=18)
            client=OCRServiceClient(os.environ['DETERMINISTIC_OCR_URL'])
            words,lines,attempts=parser.recover_ocr_coverage(page,page.rect,client,'eng',216,[],time.monotonic()+120,
                {'execution_budget_enforced':True,'recovery_cells':[[10,10,250,100]]})
            self.assertEqual(' '.join(w['text'] for w in words),'Readable sample')
            table=parser.table_entry([[10,10,250,100]],words,1,1,'test',ordered_lines=[words])
            text,errors=parser.assemble_page_text(lines,words,[table],line_word_order={id(lines[0]):words})
            self.assertEqual(table['rows'],[['Readable sample']])
            self.assertIn('Readable sample',text)
            self.assertFalse(errors)
            self.assertLessEqual(sum('reused_attempt_index' not in a and a['status']!='not_attempted' for a in attempts),4)

    def test_same_line_different_heights_and_tokenization(self):
        for scale in (.5, 1, 2):
            box=lambda b:[v*scale for v in b]
            e=embedded_comparisons([word('AlphaBeta',box([0,20,100,40]))],
                [word('Alpha',box([0,20,45,30])),word('Beta',box([55,20,100,40]))])
            self.assertEqual(e[0]['status'],'matched')

    def test_cross_cell_word_cannot_join_old_cells(self):
        e=embedded_comparisons([word('Left',[5,20,45,40]),word('Right',[55,20,95,40])],
            [word('Crossing',[5,20,95,40])],cells=[[0,0,50,80],[50,0,100,80]])
        self.assertEqual(sum(len(x['embedded_words']) for x in e),2)
        self.assertTrue(all(len(x['embedded_words'])==1 for x in e))
        self.assertTrue(all(x.get('geometry_ambiguous') for x in e))

    def test_pixel_identical_crops_share_one_request(self):
        with fitz.open() as doc:
            page=doc.new_page(width=400,height=300)
            boxes=[[134.8888855,173.8888855,296.444458,195.5],
                   [134.8888855,173.8888855,296.5245667,195.5]]
            jobs=[(fitz.Rect(b),fitz.Rect(b)) for b in boxes]
            with patch.object(parser,'uncovered_image',return_value=(boxes,None)),patch.object(
                    parser,'recovery_boxes',return_value=jobs),patch.object(
                    parser,'ocr_region',return_value=([],[],None)) as call:
                _,_,attempts=parser.recover_ocr_coverage(page,page.rect,Mock(),'eng',216,[],float('inf'),
                    {'execution_budget_enforced':True})
            self.assertEqual(call.call_count,1)
            self.assertEqual(attempts[1]['reused_attempt_index'],0)

    def test_quality_multiline_uses_block_mode(self):
        ws=[word('First',[20,20,80,30]),word('Second',[20,40,80,50])]
        with patch.object(parser,'uncovered_image',return_value=([],None)),patch.object(
                parser,'ocr_region',return_value=([],[],None)) as call:
            parser.recover_ocr_coverage(None,fitz.Rect(0,0,200,100),Mock(),'eng',216,ws,float('inf'),
                {'execution_budget_enforced':True},quality_lines=[{'bbox':[20,20,80,50]}])
        self.assertEqual(call.call_args.args[5],6)

    def test_empty_primary_uses_actual_image_line_support(self):
        with fitz.open() as doc:
            page=doc.new_page(width=240,height=100)
            page.insert_text((20,45),'Readable sample',fontsize=14)
            ws=[word(w[4],w[:4]) for w in page.get_text('words')]
            box=[min(w['bbox'][0] for w in ws),min(w['bbox'][1] for w in ws),
                 max(w['bbox'][2] for w in ws),max(w['bbox'][3] for w in ws)]
            with patch.object(parser,'uncovered_image',return_value=([box],None)),patch.object(
                    parser,'ocr_region',return_value=(ws,[{'_ordered_words':ws}],None)):
                added,_,attempts=parser.recover_ocr_coverage(page,page.rect,Mock(),'eng',216,[],float('inf'),
                    {'execution_budget_enforced':True,'recovery_cells':[[0,0,240,100]]})
            self.assertEqual([w['text'] for w in added],['Readable','sample'])


if __name__=='__main__':unittest.main()
