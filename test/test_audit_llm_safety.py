import contextlib
import importlib.util
import io
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from agent.agent_backend.llm.errors import LLMContextError, LLMExecutionError
from agent.agent_backend.llm.providers.openai_compatible import OpenAICompatibleTextModel


def load_client():
    stubs = {}
    for name, fields in {
        'agent.agent_backend.database.vector.vector_db': {'Embedding': object},
        'agent.agent_backend.llm.factory': {'ModelFactory': SimpleNamespace(chat_model=lambda: None)},
        'agent.agent_backend.llm.model_setting': {'get_active_model_name': lambda _: 'synthetic'},
    }.items():
        m = ModuleType(name)
        m.__dict__.update(fields)
        stubs[name] = m
    spec = importlib.util.spec_from_file_location('isolated_llm_client', ROOT/'agent_backend/llm/client.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, stubs):
        spec.loader.exec_module(module)
    return module.LLMClient


class ModelSafetyTests(unittest.TestCase):
    def setUp(self):
        self.Client = load_client()

    def test_no_silent_trim_and_no_provider_request(self):
        client = self.Client.__new__(self.Client)
        client.chat_context_max_chars = 10
        client._active_chat_model_name = 'synthetic'
        calls = []
        client._chat_model = SimpleNamespace(chat=lambda **kw: calls.append(kw) or 'ok')
        messages = [{'role': 'system', 'content': 'RULE'}, {'role': 'user', 'content': '123456'}]
        self.assertEqual(client._trim_messages_for_chat(messages), messages)
        client.chat(messages)
        self.assertEqual(calls[0]['messages'], messages)
        messages[1]['content'] += '7'
        for method in (client.chat, client.parse):
            with self.assertRaises(LLMContextError) as captured:
                method(messages=messages)
            self.assertEqual(captured.exception.code, 'context_limit_exceeded')
            self.assertEqual(captured.exception.actual_chars, 11)
        self.assertEqual(len(calls), 1)
        self.assertEqual(messages[1]['content'], '1234567')

    def test_invalid_budget_and_long_system(self):
        for value in ('0', '-1', 'oops'):
            with patch.dict(os.environ, {'LLM_CHAT_CONTEXT_MAX_CHARS':value}):
                with self.assertRaises(LLMContextError):
                    self.Client()
        client = self.Client.__new__(self.Client)
        client.chat_context_max_chars = 2
        with self.assertRaises(LLMContextError):
            client._trim_messages_for_chat([{'role':'system', 'content':'long'}])

    def test_provider_and_facade_logs_never_contain_payload(self):
        secret = 'SYNTHETIC_SECRET_PAYLOAD_0917'
        provider = OpenAICompatibleTextModel.__new__(OpenAICompatibleTextModel)
        provider.model = 'synthetic'
        provider.api_key = secret
        provider.base_url = f'https://user:{secret}@example.invalid/path/{secret}?key={secret}'
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
            create=lambda **kw: SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=secret))]))))
        client = self.Client.__new__(self.Client)
        client.chat_context_max_chars = 10000
        client._active_chat_model_name = 'synthetic'
        client._chat_model = provider
        client.embedding_context_max_chars = 10000
        client._embedding_model = SimpleNamespace(embed_query=lambda _: [0], convert_text_to_embedding=lambda _: [[0]])
        for verbose in ('0','1'):
            stdout, stderr = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {'LLM_VERBOSE_LOG':verbose}), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                for method in (client.chat, client.parse):
                    self.assertEqual(method(messages=[{'role':'user','content':secret}], default=secret), secret)
                client.embed(secret)
                client.batch_embed([secret])
                client.rerank(secret, [secret])
                client.parse(file='/sensitive/'+secret)
                with patch.object(provider.client.chat.completions, 'create', side_effect=RuntimeError(secret)):
                    # 必要模型失败现在必须传播；保留原来的全部日志脱敏断言。
                    with self.assertRaises(LLMExecutionError) as caught:
                        provider.chat([], default='fallback')
                    self.assertNotIn(secret, str(caught.exception))
            self.assertNotIn(secret, stdout.getvalue()+stderr.getvalue())

    def test_provider_context_rejection_is_not_fallback(self):
        provider = OpenAICompatibleTextModel.__new__(OpenAICompatibleTextModel)
        provider.model, provider.base_url, provider.api_key = 'synthetic', 'http://localhost', ''
        error = RuntimeError('sensitive provider message')
        error.code = 'context_length_exceeded'
        error.status_code = 400
        provider.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: None)))
        with patch.object(provider.client.chat.completions, 'create', side_effect=error):
            with self.assertRaises(LLMContextError) as captured:
                provider.chat([], default='{"issues":[]}')
        self.assertEqual(captured.exception.code, 'provider_context_limit')
        self.assertNotIn('sensitive', str(captured.exception))

    def test_other_provider_logs_redact_input_output_and_exception(self):
        from agent.agent_backend.llm.providers import glm_models, local_models
        secret='SYNTHETIC_PRIVATE_0917'
        stream=io.StringIO()
        with contextlib.redirect_stdout(stream),contextlib.redirect_stderr(stream):
            local_models.HashEmbeddingModel({}).embed(secret,dimensions=8)
            for cls in (local_models.LexicalRerankModel,local_models.HashRerankModel):
                cls().rerank(secret,[secret])
            with patch.object(glm_models,'_load_api_key',return_value=secret):
                model=glm_models.GLMOCRParseModel({'base_url':'https://example.invalid'})
                with patch.object(glm_models.os.path,'exists',return_value=True),patch.object(model,'_parse_local_file',side_effect=RuntimeError(secret)):
                    self.assertEqual(model.parse_layout('/private/'+secret),{})
            response=SimpleNamespace(read=lambda:('{"text":"'+secret+'"}').encode())
            class Response:
                def __enter__(self):return response
                def __exit__(self,*args):pass
            with patch.object(glm_models.request,'urlopen',return_value=Response()):
                self.assertEqual(glm_models._post_json('https://example.invalid',{ 'input':secret},secret,1)['text'],secret)
        self.assertNotIn(secret,stream.getvalue())


if __name__ == '__main__':
    unittest.main()
