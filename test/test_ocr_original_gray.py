"""原图回退必须贯通服务，不能在服务端再次模糊文字。"""
import unittest
from unittest.mock import AsyncMock, patch
import numpy as np
from test_ocr_numeric_verification import ocr, data


class OriginalGrayTests(unittest.IsolatedAsyncioTestCase):
    def test_gray_recovery_preserves_pixels_and_numeric_verification(self):
        image = np.arange(90, dtype=np.uint8).reshape(5, 6, 3)
        expected = ocr.cv2.cvtColor(image, ocr.cv2.COLOR_BGR2GRAY)
        with patch.object(ocr, 'read_image', return_value=image), patch.object(
                ocr.time, 'monotonic', return_value=10), patch.object(
                ocr.cv2, 'GaussianBlur', side_effect=AssertionError('must not blur')), patch.object(
                ocr.pytesseract, 'image_to_data', return_value=data()) as call, patch.object(
                ocr, 'verify_numeric_regions', return_value=[{'status': 'numeric_uncertain'}]) as verify:
            result = ocr.recognize(b'image', 'eng', 7, 'data', 100, preprocessing='original_gray')
        np.testing.assert_array_equal(call.call_args.args[0], expected)
        self.assertEqual(call.call_args.kwargs['timeout'], 90)
        np.testing.assert_array_equal(verify.call_args.args[0], expected)
        self.assertEqual(result['data']['numeric_verification'][0]['status'], 'numeric_uncertain')
        self.assertEqual(result['data']['preprocessing'], 'original_gray')

    async def test_unknown_mode_rejected_before_upload_read(self):
        file = AsyncMock()
        response = await ocr.run_request(file, 'eng', 6, 'data', preprocessing='unknown')
        self.assertEqual(response.status_code, 400)
        file.read.assert_not_called()

    def test_default_keeps_existing_preprocessing(self):
        image = np.zeros((5, 6, 3), dtype=np.uint8)
        with patch.object(ocr, 'read_image', return_value=image), patch.object(
                ocr.time, 'monotonic', return_value=10), patch.object(
                ocr.cv2, 'GaussianBlur', wraps=ocr.cv2.GaussianBlur) as blur, patch.object(
                ocr.pytesseract, 'image_to_data', return_value=data()), patch.object(
                ocr, 'verify_numeric_regions', return_value=[]):
            ocr.recognize(b'image', 'eng', 6, 'data', 100)
        blur.assert_called_once()


if __name__ == '__main__':
    unittest.main()
