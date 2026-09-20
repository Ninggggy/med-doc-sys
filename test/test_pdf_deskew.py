import time
import unittest
import numpy as np
import cv2
from agent.agent_backend.utils.parser.pdf_deskew import deskew_image, source_quad, restore_coordinates


class DeskewTests(unittest.TestCase):
    def test_positive_and_negative_subdegree_lines(self):
        image = np.full((900,900,3),255,np.uint8)
        for value in (100,250,400,550,700):
            cv2.line(image,(100,value),(700,value),(0,0,0),2)
            cv2.line(image,(value,100),(value,700),(0,0,0),2)
        for angle in (-2,-1,-.5,.5,1,2):
            with self.subTest(angle=angle):
                rotated = cv2.warpAffine(image,cv2.getRotationMatrix2D((450,450),angle,1),
                                         (900,900),borderValue=(255,255,255))
                result = deskew_image(rotated,time.monotonic()+10)
                self.assertIsNotNone(result)
                self.assertAlmostEqual(result[1]['angle'],-angle,delta=.1)

    def test_blank_and_straight_grid_do_not_transform(self):
        image = np.full((500,500,3),255,np.uint8)
        self.assertIsNone(deskew_image(image,time.monotonic()+10))
        for value in (50,150,250,350,450):
            cv2.line(image,(50,value),(450,value),(0,0,0),2)
            cv2.line(image,(value,50),(value,450),(0,0,0),2)
        self.assertIsNone(deskew_image(image,time.monotonic()+10))
        self.assertIsNone(deskew_image(image,time.monotonic()-1))

    def test_inverse_returns_original_points_and_nested_regions(self):
        # 独立指定变换 x'=y+20, y'=-x+100，像素尺度2。
        inverse = [[0,-1,100],[1,0,-20]]
        self.assertEqual(source_quad([15,30,25,40],inverse,2),[[20,5],[20,15],[10,15],[10,5]])
        row = {'page_bbox':[0,0,100,100], 'words':[{'bbox':[15,30,25,40]}],
               'errors':[{'bbox_pdf':[15,30,25,40], 'unclosed_bbox_pdf':[15,30,25,40],
                          'uncovered_components':[[15,30,25,40]]}]}
        row['alias'] = row['words'][0]
        restore_coordinates(row,inverse,2,[0,0,80,80])
        self.assertEqual(row['words'][0]['bbox'],[10,5,20,15])
        self.assertEqual(row['words'][0]['bbox_deskewed'],[15,30,25,40])
        self.assertEqual(row['errors'][0]['uncovered_components'],[[10,5,20,15]])
        self.assertEqual(row['errors'][0]['unclosed_bbox_pdf'],[10,5,20,15])
        self.assertEqual(row['errors'][0]['unclosed_bbox_pdf_deskewed'],[15,30,25,40])
        self.assertEqual(row['page_bbox'],[0,0,80,80])


if __name__ == '__main__':
    unittest.main()
