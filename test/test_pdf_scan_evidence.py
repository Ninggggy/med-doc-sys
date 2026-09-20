"""旧层不作为答案；使用真实绘制、文字轨迹和栅格验证通用行为。"""
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch,Mock
import fitz
from agent.agent_backend.utils.parser import pdf_page_extractor as parser
from agent.agent_backend.utils.parser.pdf_scan_evidence import (
    partition_text, embedded_comparisons, finalize_embedded_evidence)


def word(text,box=(10,10,80,25),**extra):
    return {'text':text,'bbox':list(box),'confidence':95,**extra}


class ScanEvidenceTests(unittest.TestCase):
    def test_remaining_crop_budget_and_text_priority_are_observed(self):
        for limit in (0,1):
            with patch.object(parser,'uncovered_image',return_value=([[10,100,180,150]],None)), patch.object(
                    parser,'ocr_region',return_value=([],[],None)) as call, patch.object(parser.time,'monotonic',return_value=10):
                _,_,attempts=parser.recover_ocr_coverage(None,fitz.Rect(0,0,200,200),Mock(),
                    'eng',216,[],100,{'execution_budget_enforced':True,'remaining_recovery_regions':limit},
                    quality_lines=[{'bbox':[10,20,60,30]}])
            self.assertEqual(call.call_count,limit)
            self.assertEqual(len(attempts),2)
            if limit:
                self.assertLess(call.call_args.args[1].y1,50)
            self.assertTrue(any(a.get('reason_code')=='recovery_region_limit' for a in attempts))

    def test_mixed_visible_and_hidden_text_uses_drawing_trace(self):
        with fitz.open() as doc:
            page=doc.new_page()
            page.insert_text((20,40),'Visible current')
            page.insert_text((20,80),'Outdated hidden',render_mode=3)
            words=[word(w[4],w[:4]) for w in page.get_text('words')]
            visible,lines,hidden=partition_text(page,words,parser.native_lines(page))
        self.assertEqual(' '.join(w['text'] for w in visible),'Visible current')
        self.assertEqual(' '.join(w['text'] for w in hidden),'Outdated hidden')
        self.assertEqual(lines[0]['text'],'Visible current')

    def test_different_tokenization_preserves_same_text(self):
        old=[word('AlphaBeta',(10,10,90,25))]
        new=[word('Alpha',(10,10,50,25)),word('Beta',(50,10,90,25))]
        evidence=embedded_comparisons(old,new)
        self.assertEqual(len(evidence),1)
        self.assertEqual(evidence[0]['status'],'matched')
        self.assertEqual(len(evidence[0]['primary_words']),2)

    def test_small_scan_region_does_not_make_hidden_text_trusted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'mixed.pdf'
            with fitz.open() as scan,fitz.open() as doc:
                s=scan.new_page(width=200,height=100)
                s.insert_text((20,40),'Scanned actual')
                page=doc.new_page(width=500,height=500)
                page.insert_text((20,40),'Visible original')
                page.insert_image((20,100,220,200),stream=s.get_pixmap().tobytes('png'))
                page.insert_text((40,140),'Hidden outdated',render_mode=3)
                doc.save(path)
            calls=[]
            def recognize(page,rect,*args,**kwargs):
                calls.append(args[4] if len(args)>4 else ())
                w=word('Scanned actual',(40,128,150,143),source='ocr')
                return [w],[w],page.get_pixmap(matrix=fitz.Matrix(3,3),clip=rect)
            with patch.object(parser,'ocr_region',side_effect=recognize),patch.object(parser,'recover_ocr_coverage',return_value=([],[],[])):
                pages=parser.extract_pdf_pages(str(path),ocr_client=Mock(timeout_seconds=120))
            self.assertIn('Visible original',pages[0]['text'])
            self.assertIn('Scanned actual',pages[0]['text'])
            self.assertNotIn('outdated',pages[0]['text'])
            self.assertTrue(pages[0]['embedded_text_candidates'])
            self.assertTrue(all('Hidden' not in w['text'] for call in calls for w in call))
            with patch.object(parser,'ocr_region',side_effect=TimeoutError('test deadline')):
                failed=parser.extract_pdf_pages(str(path),ocr_client=Mock(timeout_seconds=120))[0]
            self.assertTrue(failed['embedded_ocr_evidence'])
            represented=[w for e in failed['embedded_ocr_evidence'] for w in e['embedded_words']]
            self.assertEqual(len(represented),len(failed['embedded_text_candidates']))
            self.assertTrue(all(e['status']=='missing' for e in failed['embedded_ocr_evidence']))

    def test_missing_and_conflict_are_not_silently_resolved(self):
        for new,state in [([], 'missing'),([word('changed')],'conflict')]:
            evidence=embedded_comparisons([word('old')],new)
            self.assertEqual(evidence[0]['status'],state)
            self.assertEqual(len(finalize_embedded_evidence(evidence,[])),1)
            self.assertEqual(evidence[0]['embedded_text'],'old')

    def test_primary_and_independent_crop_can_revalidate_old_error(self):
        evidence=embedded_comparisons([word('old')],[word('actual')])
        attempt={'bbox_pdf':[0,0,100,40],'candidates':[word('actual')]}
        self.assertEqual(finalize_embedded_evidence(evidence,[attempt]),[])
        self.assertEqual(evidence[0]['status'],'revalidated')
        self.assertEqual(evidence[0]['embedded_words'][0]['text'],'old')

    def test_low_confidence_or_numeric_disagreement_does_not_revalidate(self):
        for candidate in [word('3 mg',confidence=79),word('3 mg',numeric_verification={'status':'numeric_uncertain'})]:
            evidence=embedded_comparisons([word('8 mg')],[word('3 mg')])
            self.assertTrue(finalize_embedded_evidence(evidence,[{'bbox_pdf':[0,0,100,40],'candidates':[candidate]}]))

    def test_missing_text_requires_image_candidate_actually_adopted(self):
        candidate=word('Recovered')
        attempt={'bbox_pdf':[0,0,100,40],'candidates':[candidate]}
        evidence=embedded_comparisons([candidate],[])
        self.assertTrue(finalize_embedded_evidence(evidence,[attempt]))
        self.assertFalse(finalize_embedded_evidence(evidence,[attempt],[candidate]))
        self.assertEqual(evidence[0]['status'],'revalidated')

    def test_scan_table_survives_invisible_text_and_empty_ocr(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'scan.pdf'
            with fitz.open() as original:
                p=original.new_page(width=300,height=200)
                for x in [20,110,200,280]:p.draw_line((x,40),(x,160))
                for y in [40,80,120,160]:p.draw_line((20,y),(280,y))
                pix=p.get_pixmap(matrix=fitz.Matrix(2,2))
                with fitz.open() as doc:
                    p=doc.new_page(width=300,height=200)
                    p.insert_image(p.rect,stream=pix.tobytes('png'))
                    p.insert_text((25,65),'outdated hidden',render_mode=3)
                    doc.save(path)
            def empty(page,rect,*args,**kwargs):return [],[],page.get_pixmap(matrix=fitz.Matrix(3,3))
            with patch.object(parser,'ocr_region',side_effect=empty):
                pages=parser.extract_pdf_pages(str(path),ocr_client=Mock(timeout_seconds=120))
            self.assertEqual(len(pages[0]['tables']),1)
            self.assertEqual((len(pages[0]['tables'][0]['rows']),len(pages[0]['tables'][0]['columns'])),(3,3))
            self.assertEqual(pages[0]['status'],'failed')
            self.assertNotIn('outdated',pages[0]['text'])

    def test_red_stamp_joined_to_header_does_not_invent_a_column(self):
        with fitz.open() as doc:
            p=doc.new_page(width=300,height=200)
            for x in [20,110,200,280]:p.draw_line((x,40),(x,160))
            for y in [40,80,120,160]:p.draw_line((20,y),(280,y))
            p.draw_rect((45,20,50,85),color=(1,0,0),fill=(1,0,0))
            groups=parser.raster_cells(p.get_pixmap(matrix=fitz.Matrix(3,3)),(0,0),3)
            self.assertEqual(len(groups),1)
            self.assertEqual(len(groups[0]),9)
            table=parser.table_entry(groups[0],[],1,1,'test')
            self.assertEqual(len(table['columns']),3)

    def test_colored_independent_table_is_not_removed(self):
        with fitz.open() as doc:
            p=doc.new_page(width=300,height=200)
            for x in [20,150,280]:p.draw_line((x,40),(x,160),color=(1,0,0))
            for y in [40,100,160]:p.draw_line((20,y),(280,y),color=(1,0,0))
            groups=parser.raster_cells(p.get_pixmap(matrix=fitz.Matrix(3,3)),(0,0),3)
            self.assertEqual(len(groups),1)
            self.assertEqual(len(groups[0]),4)

    def test_partial_header_dividers_are_not_removed_by_regular_projection(self):
        from test_pdf_page_extraction import gradient_page
        with fitz.open() as doc:
            page=gradient_page(doc)
            groups=parser.raster_cells(page.get_pixmap(matrix=fitz.Matrix(3,3)),(0,0),3)
            self.assertEqual(len(groups),1)
            table=parser.table_entry(groups[0],[],1,1,'test')
            self.assertEqual((len(table['rows']),len(table['columns'])),(4,3))
            self.assertEqual(len(table['cells']),10)
            self.assertEqual(table['cells'][0].get('content_state'),'empty_unverified')


if __name__=='__main__':unittest.main()
