"""证据共享只减少重复几何，不减少诊断、候选或原图定位。"""
import json
import unittest
from copy import deepcopy
from agent.agent_backend.utils.parser.pdf_scan_evidence import compact_recovery_geometry, recovery_geometry


class EvidenceCompactionTests(unittest.TestCase):
    def test_roundtrip_preserves_all_evidence_including_unattempted(self):
        support = [{'bbox_pdf':[1,2,3,4], 'component_bboxes':[[1,2,2,3]]}]
        attempts = [dict(pixel_window=[0,0,100,100], recovery_dpi=216,
                         segmentation_psm=6, image_line_support=deepcopy(support),
                         candidate_lines=[{'text':'different words', 'bbox':[1,2,3,4]}],
                         candidates=[{'text':str(i), 'bbox':[1,2,3,4]}],
                         status='not_attempted' if i else 'unresolved') for i in range(4)]
        before = deepcopy(attempts)
        compact_recovery_geometry(attempts)
        loaded = json.loads(json.dumps(attempts))
        for i, item in enumerate(loaded):
            for field in ('image_line_support', 'candidate_lines'):
                self.assertEqual(recovery_geometry(loaded, i, field), before[i][field])
            self.assertEqual(item['candidates'], before[i]['candidates'])
            self.assertEqual(item['status'], before[i]['status'])
        self.assertEqual(loaded[3]['image_line_support_attempt_index'], 0)
        compact_recovery_geometry(loaded)
        self.assertEqual(loaded, attempts)

    def test_different_window_mode_scale_or_geometry_not_shared(self):
        original = dict(pixel_window=[0,0,10,10], recovery_dpi=216, segmentation_psm=6,
                        image_line_support=[{'bbox_pdf':[1,2,3,4]}])
        for change in ({'pixel_window':[1,0,11,10]}, {'recovery_dpi':300},
                       {'segmentation_psm':7}, {'ocr_input_size':[5,5]},
                       {'image_line_support':[{'bbox_pdf':[1,2,3,5]}]}):
            values = [deepcopy(original), {**deepcopy(original), **change}]
            before = deepcopy(values)
            compact_recovery_geometry(values)
            self.assertEqual(values, before)

    def test_legacy_and_invalid_reference(self):
        values = [{'image_line_support':[1]}, {'image_line_support_attempt_index':8}]
        self.assertEqual(recovery_geometry(values, 0, 'image_line_support'), [1])
        self.assertEqual(recovery_geometry(values, 1, 'image_line_support'), [])

    def test_deskew_maps_shared_geometry_exactly_once(self):
        from agent.agent_backend.utils.parser.pdf_deskew import restore_coordinates
        attempt = dict(pixel_window=[0,0,100,100], recovery_dpi=216, segmentation_psm=6,
                       image_line_support=[{'bbox_pdf':[2,3,10,12],
                                            'component_bboxes':[[3,4,5,7]]}])
        row = dict(page_bbox=[0,0,100,100], ocr_recovery=[deepcopy(attempt),deepcopy(attempt)])
        original = deepcopy(row)
        inverse = [[1,0,3],[0,1,6]]
        restore_coordinates(original, inverse, 3, [0,0,100,100])
        compact_recovery_geometry(row['ocr_recovery'])
        restore_coordinates(row, inverse, 3, [0,0,100,100])
        for index in range(2):
            self.assertEqual(recovery_geometry(row['ocr_recovery'],index,'image_line_support'),
                             original['ocr_recovery'][index]['image_line_support'])


if __name__ == '__main__':
    unittest.main()
