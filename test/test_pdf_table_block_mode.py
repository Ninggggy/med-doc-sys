import unittest
import numpy as np
from agent.agent_backend.utils.parser.pdf_page_extractor import table_block_mode


class TableBlockModeTests(unittest.TestCase):
    def test_small_body_with_one_large_heading_keeps_auto_layout(self):
        image = np.full((600, 800, 3), 255, dtype=np.uint8)
        groups = [[[50, 50, 400, 250], [400, 50, 750, 250],
                   [50, 250, 400, 500], [400, 250, 750, 500]]]
        image[70:142, 80:100] = 0
        for y in (180, 300, 380):
            for x in range(80, 700, 40):
                image[y:y+24, x:x+12] = 0
        self.assertEqual(table_block_mode(image, groups, 1, 3), 3)

    def test_single_table_without_side_content(self):
        image = np.full((400, 300, 3), 255, dtype=np.uint8)
        for y in (50, 150):
            for x in (70, 110, 170, 210):
                image[y:y+72, x:x+20] = 0
        self.assertEqual(table_block_mode(image, [[[50, 30, 150, 250], [150, 30, 250, 250]]], 1, 3), 6)

    def test_side_content_multiple_tables_and_explicit_modes_stay_unchanged(self):
        image = np.full((200, 300, 3), 255, dtype=np.uint8)
        groups = [[[50, 30, 150, 80], [150, 30, 250, 80]]]
        image[20:30, 10:20] = 0
        self.assertEqual(table_block_mode(image, groups, 1, 3), 3)
        self.assertEqual(table_block_mode(image, [], 1, 3), 3)
        self.assertEqual(table_block_mode(image, groups * 2, 1, 3), 3)
        self.assertEqual(table_block_mode(image, groups, 1, 7), 7)

    def test_coordinates_are_scaled(self):
        image = np.full((400, 300, 3), 255, dtype=np.uint8)
        for y in (50, 150):
            for x in (70, 110, 170, 210):
                image[y:y+72, x:x+20] = 0
        groups = [[[25, 15, 75, 125], [75, 15, 125, 125]]]
        self.assertEqual(table_block_mode(image, groups, 2, 3), 6)
        self.assertEqual(table_block_mode(image, groups, 1, 3), 3)
