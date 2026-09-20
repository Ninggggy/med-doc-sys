"""真实本地HTTP、合成识别结果；不代表真实OCR准确性或生产性能。"""
import json
import re
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import requests
from agent.agent_backend.utils.parser import ctd_paser, drug_supplement_pdf_parser


class BudgetHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calls = []
        cls.payload = {'text': ['synthetic'], 'left': [0], 'top': [0],
                       'width': [10], 'height': [10], 'conf': [90],
                       'numeric_verification': [{'primary': '1.0', 'secondary': '',
                           'status': 'numeric_uncertain',
                           'reason_code': 'numeric_verification_budget_exhausted'}]}

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                content = self.rfile.read(int(self.headers['Content-Length']))
                match = re.search(rb'name="execution_budget_seconds"\r\n\r\n([^\r]+)', content)
                budget = float(match.group(1))
                cls.calls.append(budget)
                # 模拟识别在预算末结束，加上真实系统不可省略的回收/序列化时间。
                time.sleep(budget + .04)
                body = json.dumps({'data': cls.payload}).encode()
                try:
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = 'http://127.0.0.1:%s' % cls.server.server_port

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def test_old_equal_deadlines_lose_response(self):
        for module in (ctd_paser, drug_supplement_pdf_parser):
            with self.subTest(module=module.__name__):
                with patch.object(module, 'request_budgets', return_value=(2., 2.)):
                    with self.assertRaises(requests.exceptions.ReadTimeout):
                        module.OCRServiceClient(self.url).image_to_data(
                            b'synthetic', 'eng', execution_budget_seconds=2.)

    def test_reserved_return_time_preserves_uncertain_content_without_retry(self):
        for module in (ctd_paser, drug_supplement_pdf_parser):
            with self.subTest(module=module.__name__):
                count = len(self.calls)
                result = module.OCRServiceClient(self.url).image_to_data(
                    b'synthetic', 'eng', execution_budget_seconds=2.)
                self.assertEqual(result, self.payload)
                self.assertEqual(len(self.calls), count + 1)
                self.assertAlmostEqual(self.calls[-1], 1.9)
