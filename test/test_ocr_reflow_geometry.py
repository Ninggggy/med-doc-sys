"""独立坐标例验证原图多行来源不被扩成大包围框。"""
import unittest
from copy import deepcopy
from agent.agent_backend.utils.parser.ocr_reflow_geometry import source_fragments, reflow_lines


class ReflowGeometryTests(unittest.TestCase):
    def test_recognition_keeps_numeric_candidate_uncertain_and_separate_sources(self):
        from unittest.mock import Mock, patch
        import fitz
        from agent.agent_backend.utils.parser import ocr_reflow_geometry as module
        client = Mock(); client.supports_execution_budget = True; client.supports_preprocessing = True
        client.image_to_data.return_value = {'text':['1.5'],'left':[20],'top':[20],
            'width':[30],'height':[10],'conf':[99],'preprocessing':'original_gray',
            'execution_budget_enforced':True,'numeric_verification':[{'word_indices':[0],'status':'verified'}]}
        with fitz.open() as doc, patch.object(__import__('time'),'monotonic',return_value=10):
            page = doc.new_page(width=60,height=80)
            page.draw_rect(fitz.Rect(5,5,20,15),color=None,fill=(0,0,0))
            page.draw_rect(fitz.Rect(5,30,20,40),color=None,fill=(0,0,0))
            rows = module.recognize_cell_candidates(page,page.rect,client,'eng',72,100)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(rows[0]['source_bboxes_pdf']),2)
        self.assertNotIn('bbox',rows[0])
        self.assertTrue(rows[0]['needs_review'])
        self.assertEqual(rows[0]['numeric_verification']['status'],'numeric_uncertain')
        self.assertEqual(rows[0]['numeric_verification']['original_evidence'][0]['status'],'verified')
        client.image_to_data.assert_called_once()
        self.assertEqual(client.image_to_data.call_args.kwargs['execution_budget_seconds'],90)

    def test_reflow_preserves_source_pixels_and_does_not_mutate_original(self):
        from PIL import Image
        image = Image.new('L',(20,30),255)
        for y in range(3,8):
            for x in range(2,9):
                image.putpixel((x,y),50+x+y)
        for y in range(20,26):
            for x in range(5,14):
                image.putpixel((x,y),x+y)
        image.putpixel((1,3),240)
        before = image.tobytes()
        output,mapping = reflow_lines(image)
        self.assertEqual(len(mapping),2)
        for piece in mapping:
            self.assertEqual(image.crop(piece['source_pixel_bbox']).tobytes(),
                             output.crop(piece['reflow_pixel_bbox']).tobytes())
        self.assertEqual(image.tobytes(),before)
        self.assertEqual(sum(value<255 for value in output.tobytes()),
                         sum(value<255 for value in before))

    def test_blank_and_single_line_do_not_need_reflow(self):
        from PIL import Image
        image = Image.new('L',(20,30),255)
        self.assertIsNone(reflow_lines(image))
        image.putpixel((5,5),0)
        self.assertIsNone(reflow_lines(image))

    def test_reflow_requires_valid_mode_spacing_and_bounded_output(self):
        from PIL import Image
        image = Image.new('L',(20,30),255)
        image.putpixel((5,5),0)
        image.putpixel((5,20),0)
        with self.assertRaises(ValueError):
            reflow_lines(image.convert('RGB'))
        with self.assertRaises(ValueError):
            reflow_lines(image,gap=-1)
        with self.assertRaises(ValueError):
            reflow_lines(image,max_pixels=600)

    def setUp(self):
        self.mapping = [
            {'source_pixel_bbox':[10,10,30,20],'reflow_pixel_bbox':[20,20,40,30]},
            {'source_pixel_bbox':[10,40,30,50],'reflow_pixel_bbox':[48,20,68,30]}]

    def test_cross_line_word_retains_two_disjoint_sources(self):
        before = deepcopy(self.mapping)
        self.assertEqual(source_fragments([35,22,53,28],self.mapping),[
            {'piece_index':0,'source_pixel_bbox':[25,12,30,18]},
            {'piece_index':1,'source_pixel_bbox':[10,42,15,48]}])
        self.assertEqual(before,self.mapping)

    def test_white_gap_has_no_source(self):
        self.assertEqual(source_fragments([41,22,47,28],self.mapping),[])

    def test_padding_is_not_mapped_into_source(self):
        self.assertEqual(source_fragments([0,0,25,25],self.mapping),[
            {'piece_index':0,'source_pixel_bbox':[10,10,15,15]}])

    def test_invalid_and_nonfinite_boxes_rejected(self):
        for box in ([0,0,0,10],[0,0,float('nan'),10],[False,0,10,10],['0',0,10,10]):
            with self.subTest(box=box),self.assertRaises(ValueError):
                source_fragments(box,self.mapping)

    def test_overlapping_source_or_destination_rejected(self):
        for field in ('source_pixel_bbox','reflow_pixel_bbox'):
            mapping = deepcopy(self.mapping)
            mapping[1][field] = mapping[0][field]
            with self.subTest(field=field),self.assertRaises(ValueError):
                source_fragments([20,20,30,30],mapping)

    def test_scaling_cannot_be_silently_treated_as_translation(self):
        mapping = deepcopy(self.mapping)
        mapping[0]['reflow_pixel_bbox'][2] += 1
        with self.assertRaises(ValueError):
            source_fragments([20,20,30,30],mapping)


if __name__ == '__main__':
    unittest.main()
