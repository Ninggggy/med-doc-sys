"""检测范围不得被深色连通域收缩；实际识别效果另行验证。"""
import copy
import sys
import tempfile
import unittest
from pathlib import Path
from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'ocr_service' / 'paddle_runtime'))
from bounded_review import complete_cell_line_input
from unittest.mock import Mock, patch
from bounded_review import review_unresolved_cell_overlays, complete_final_sequences


class CellDetectionExtent(unittest.TestCase):
    def test_faint_strokes_keep_full_detector_extent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            im = Image.new('RGB', (100, 60), 'white')
            draw = ImageDraw.Draw(im)
            draw.rectangle([10, 20, 90, 40], outline=(190, 190, 190))
            draw.rectangle([20, 25, 80, 35], fill='black')
            im.save(root / 'source.png')
            poly = [[10, 20], [90, 20], [90, 40], [10, 40]]
            span = dict(span_id='body', text_raw='测试', polygon_page=poly,
                        polygon_input_px=poly, coordinate_source='engine_polygon')
            result = dict(input=dict(image=str(root / 'source.png'),
                          input_to_page=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
                          input_size=[100, 60], spans=[span])
            candidate = copy.deepcopy(result)
            cell = dict(result=result, content_candidates=[candidate])
            geometry, _ = complete_cell_line_input(cell, candidate['spans'][0],
                                                   root / 'prepared.png', candidate)
            self.assertEqual(geometry['target_polygon_page'], poly)
            self.assertEqual(geometry['source_bbox_px'], [4, 14, 96, 46])
            self.assertTrue(geometry['pixel_preservation']['eligible'])
            self.assertEqual(geometry['review_scope'], 'cell_body')
            self.assertEqual(Image.open(root / 'source.png').tobytes(), im.tobytes())

    def test_partial_observation_is_not_a_whole_line_deletion_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            im = Image.new('RGB', (100, 60), 'white')
            ImageDraw.Draw(im).rectangle([15, 24, 85, 36], fill='black')
            im.save(root / 'source.png')
            def span(sid, text, lo, hi):
                poly = [[lo, 20], [hi, 20], [hi, 40], [lo, 40]]
                return dict(span_id=sid, text_raw=text, polygon_page=poly,
                            polygon_input_px=poly, coordinate_source='engine_polygon')
            original = dict(input=dict(image=str(root / 'source.png'),
                            input_to_page=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]),
                            input_size=[100, 60], spans=[span('fragment', '尾', 70, 90)])
            candidate = copy.deepcopy(original)
            candidate['spans'] = [span('line', '完整文字', 10, 90)]
            short_full = copy.deepcopy(original)
            short_full['spans'] = [span('short-full-line', 'X', 10, 90)]
            cell = dict(result=original, content_candidates=[candidate, short_full])
            _, candidates = complete_cell_line_input(cell, candidate['spans'][0],
                                                     root / 'input.png', candidate)
            self.assertNotIn('尾', candidates)
            self.assertIn('X', candidates)  # 不按字数或预期格式排除真正同范围结果。
            self.assertEqual(original['spans'][0]['text_raw'], '尾')

    def test_requested_primary_failure_does_not_replace_existing_cell_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            span = dict(text_raw='旧正文')
            page = dict(page_result={}, tables=[dict(cells=[dict(content_candidates=[dict(spans=[span])])])])
            old = dict(text='既有候选')
            entries = {(0, 0): old}
            geometry = dict(seal_overlay_role={'regions': []})
            response = dict(text='新候选', primary_sequence_failure={'reason': 'output file collision'})
            model = Mock()
            model.predict.return_value = response
            with patch('bounded_review.decide_cell', return_value={'resolved': False}), \
                 patch('bounded_review.cell_review_input', return_value=(geometry, ['旧正文'])), \
                 patch('auxiliary_recognition.AuxiliaryRecognizer', return_value=model), \
                 patch('bounded_review.complete_sequence_reviews'), \
                 patch('bounded_review.complete_final_sequences', return_value=0), \
                 patch('sequence_adjudication.decide_sequence', return_value={
                       'accepted': True, 'whole_sequence_review': {'complete': True}}) as decide:
                review_unresolved_cell_overlays(page, {(0, 0): entries}, 'existing-model', directory, Mock(), 1)
            self.assertIs(entries[(0, 0)], old)
            self.assertEqual(old['original_color_review'], response)
            decide.assert_not_called()
            model.close.assert_called_once()

    def test_full_cell_line_primary_decode_participates_in_scoring(self):
        geometry = {'review_scope': 'cell_body'}
        response = dict(image='existing-input.png', source_geometry=geometry,
            sequence_evidence={'primary_text': 'AB', 'candidate_texts': ['AB', 'AC']},
            primary_sequence_review=dict(status='completed', image='existing-input.png',
                                         source_geometry=geometry, text='AD'))
        model = Mock()
        model.predict.return_value = dict(sequence_evidence={'primary_text': 'AB'},
                                          source_geometry=geometry)
        with patch('auxiliary_recognition.AuxiliaryRecognizer', return_value=model), \
             patch('bounded_review.complete_sequence_reviews'):
            count = complete_final_sequences({}, [('line', response)], Mock(), 'existing-model')
        self.assertEqual(count, 1)
        self.assertEqual(model.predict.call_args.args[1], ['AB', 'AC', 'AD'])
        self.assertEqual(model.predict.call_args.args[0], 'existing-input.png')


if __name__ == '__main__':
    unittest.main()
