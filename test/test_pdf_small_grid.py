"""用实际渲染图核对小表拓扑，不用单元格数量代表正确性。"""
import unittest
import fitz
import numpy as np
from agent.agent_backend.utils.parser import pdf_page_extractor as parser


class SmallGridTests(unittest.TestCase):
    def test_open_tail_endpoint_raster_difference_not_internal_gap(self):
        import cv2
        from unittest.mock import patch
        for difference, gap, accepted in ((1,False,True),(10,False,False),(1,True,False)):
            with self.subTest(difference=difference,gap=gap):
                grid = np.zeros((300,400),np.uint8)
                for y in (30,90,150):
                    cv2.line(grid,(30,y),(330,y),255,2)
                for x in (30,130,230,330):
                    end = 220 if x==330 else 220-difference
                    cv2.line(grid,(x,30),(x,end),255,2)
                if gap:
                    grid[180:190,128:133]=0
                issues=[]
                with patch.object(parser,'raster_grid',return_value=(None,grid)):
                    groups=parser.raster_cells(None,(0,0),1,incomplete_regions=issues)
                self.assertEqual(bool(groups),accepted)
                self.assertEqual(bool(issues),accepted)
                if accepted:
                    self.assertEqual(len(groups[0]),6)

    def cells(self, segments):
        with fitz.open() as doc:
            page = doc.new_page(width=240, height=150)
            for start, end in segments:
                page.draw_line(start, end)
            return parser.raster_cells(page.get_pixmap(matrix=fitz.Matrix(3, 3)), (0, 0), 3)

    def outline(self):
        return [((20, 20), (220, 20)), ((220, 20), (220, 120)),
                ((220, 120), (20, 120)), ((20, 120), (20, 20))]

    def test_two_cell_table_has_real_internal_divider(self):
        groups = self.cells(self.outline() + [((120, 20), (120, 120))])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)
        table = parser.table_entry(groups[0], [], 1, 1, 'ocr_grid')
        self.assertEqual(len(table['rows']), 1)
        self.assertEqual(len(table['rows'][0]), 2)

    def test_three_cells_preserve_merged_header(self):
        groups = self.cells(self.outline() + [((20, 70), (220, 70)), ((120, 70), (120, 120))])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 3)
        table = parser.table_entry(groups[0], [], 1, 1, 'ocr_grid')
        header = next(c for c in table['cells'] if c['row'] == 0)
        self.assertEqual(header['colspan'], 2)

    def test_short_broken_divider_with_outer_intersections(self):
        groups = self.cells(self.outline() + [((120, 20), (120, 69)), ((120, 71), (120, 120))])
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]), 2)

    def test_large_gap_is_not_invented_as_a_divider(self):
        groups = self.cells(self.outline() + [((120, 20), (120, 60)), ((120, 80), (120, 120))])
        self.assertEqual(groups, [])

    def test_gap_repair_requires_intersections_and_no_foreign_ink(self):
        h = np.zeros((50, 150), dtype=np.uint8)
        v = h.copy()
        h[25, 10:71] = 255
        h[25, 75:140] = 255
        v[5:45, 10] = 255
        v[5:45, 139] = 255
        ink = h | v
        repaired = parser.repair_short_grid_gaps(h, v, ink)
        self.assertTrue(repaired[25, 71:75].all())
        # 两个轴方向使用相同证据，不修改输入。
        self.assertTrue(parser.repair_short_grid_gaps(v.T, h.T, ink.T)[71:75, 25].all())
        self.assertFalse(ink[25, 71:75].any())
        missing_cross = v.copy()
        missing_cross[:, 139] = 0
        self.assertFalse(parser.repair_short_grid_gaps(h, missing_cross, ink)[25, 71:75].any())
        ink[24, 73] = 255
        self.assertFalse(parser.repair_short_grid_gaps(h, v, ink)[25, 71:75].any())

    def test_single_decorative_frame_is_not_a_table(self):
        self.assertEqual(self.cells(self.outline()), [])

    def test_open_shape_is_not_a_closed_table(self):
        self.assertEqual(self.cells(self.outline()[:-1] + [((120, 20), (120, 120))]), [])

    def test_rounded_decorations_do_not_supply_rectangular_cells(self):
        with fitz.open() as doc:
            page = doc.new_page(width=240, height=150)
            page.draw_oval((20, 20, 120, 120))
            page.draw_oval((120, 20, 220, 120))
            self.assertEqual(parser.raster_cells(page.get_pixmap(matrix=fitz.Matrix(3, 3)), (0, 0), 3), [])


if __name__ == '__main__':
    unittest.main()
