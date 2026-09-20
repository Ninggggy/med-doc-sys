"""在既有请求期限内为OCR结果传输和子进程回收留出时间。"""
import math


def request_budgets(configured_timeout, remaining=None):
    values = [float(configured_timeout)]
    if remaining is not None:
        values.append(float(remaining))
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError('invalid_ocr_request_budget')
    request_timeout = min(values)
    # 执行期限必须早于客户端等候期限。仍在原期限内分配，不扩大超时；
    # 短请求按比例预留，避免固定扣减使剩余预算变成非正数。
    response_reserve = min(2.0, request_timeout * 0.05)
    return request_timeout, request_timeout - response_reserve
