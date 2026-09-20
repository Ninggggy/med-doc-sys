"""可安全持久化的模型错误；不保存服务响应、密钥或申报正文。"""


class LLMExecutionError(RuntimeError):
    """模型未完成必要调用或未返回有效输出，不能转换为空业务结论。"""
    def __init__(self, code="model_call_failed", stage="chat", http_status=None, retryable=False):
        self.code = code
        self.stage = stage
        self.http_status = http_status
        self.retryable = bool(retryable)
        labels = {
            "model_auth_failed": "模型服务认证失败，请核对部署配置",
            "model_rate_limited": "模型服务限流，本次调用未完成",
            "model_service_error": "模型服务异常，本次调用未完成",
            "model_connection_failed": "无法连接模型服务",
            "model_timeout": "模型调用超时",
            "model_empty_output": "模型返回空内容",
            "model_invalid_json": "模型返回内容不是有效JSON",
            "model_invalid_output": "模型返回内容缺少必要字段或结构无效",
            "model_configuration_invalid": "模型配置不完整，本次未调用模型",
            "model_call_failed": "模型调用失败",
        }
        super().__init__(f"{code}: {labels.get(code, labels['model_call_failed'])}，审评未完成。")

    def as_dict(self):
        return {"code": self.code, "stage": self.stage, "http_status": self.http_status,
                "retryable": self.retryable}


class LLMContextError(ValueError):
    def __init__(self, code="context_limit_exceeded", actual_chars=None, limit_chars=None, stage="chat"):
        self.code = code
        self.actual_chars = actual_chars
        self.limit_chars = limit_chars
        self.stage = stage
        messages = {
            "context_limit_exceeded": "本次资料超过上下文字符预算，审评未完成。请缩小审评范围，或由部署人员核实模型容量后调整配置。",
            "context_budget_invalid": "上下文字符预算配置无效，必须为正整数；本次未调用模型。",
            "provider_context_limit": "模型服务拒绝了超出其上下文窗口的请求，审评未完成；字符预算并不等于模型token窗口。",
        }
        detail = f" actual_chars={actual_chars} limit_chars={limit_chars}" if actual_chars is not None else ""
        super().__init__(f"{code}: {messages[code]}{detail}")

    def as_dict(self):
        return {"code": self.code, "actual_chars": self.actual_chars,
                "limit_chars": self.limit_chars, "stage": self.stage}
