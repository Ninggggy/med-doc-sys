"""执行真实参考资料解析方法，隔离数据库/文件适配器，不启动应用或模型。"""
import ast
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock

from agent.agent_backend.services.filing_parse_outcome import outcome, failure, parse_task_id

source = Path(__file__).resolve().parents[1] / 'agent_backend/services/filing_change_review_service.py'
tree = ast.parse(source.read_text())
service_class = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == 'FilingChangeReviewService')
method = next(x for x in service_class.body if isinstance(x, ast.FunctionDef) and x.name == 'parse_reference_material')
module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), method], type_ignores=[])
scope = dict(outcome=outcome, failure=failure, parse_task_id=parse_task_id,
             log_diagnostics=Mock(), log_failure=Mock(), FilingChangeReferenceMaterial=SimpleNamespace(doc_id='id'))
exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), scope)


class ReferenceParseOutcomesTest(TestCase):
    def service(self, rows):
        row = SimpleNamespace(storage_path='sample', title='sample', parse_status='success', index_status='success')
        session = Mock()
        session.query.return_value.filter.return_value.first.return_value = row
        manifest = {'doc_meta': {'id': {'parse_status': 'success', 'extracted_json': {'old': 'retained'}}}}
        service = Mock(root_dir=Path('/unused'), db_conn=Mock(get_session=Mock(return_value=session)))
        service._reference_manifest_lock.side_effect = nullcontext
        service.material_service.parse_file.return_value = rows
        service._load_reference_manifest.side_effect = lambda: manifest
        service._json.return_value = '{}'
        service._build_submission_markdown.return_value = 'body'
        service._extract_structured_payload.return_value = {'new': 'content'}
        return service, session, manifest

    def test_partial_result_is_not_promoted_to_success(self):
        service, session, manifest = self.service([{'page': 1, 'text': 'available', 'errors': [{'stage': 'ocr_quality'}]}])
        ok, _, data = scope['parse_reference_material'](service, 'id')
        self.assertTrue(ok)
        self.assertEqual(data['content_status'], 'partial')
        self.assertEqual(manifest['doc_meta']['id']['latest_attempt']['content_status'], 'partial')
        session.commit.assert_called_once()

    def test_failure_preserves_previous_content_and_reports_latest_attempt(self):
        service, session, manifest = self.service([])
        ok, _, data = scope['parse_reference_material'](service, 'id')
        self.assertFalse(ok)
        self.assertEqual(data['content_status'], 'failed')
        self.assertEqual(manifest['doc_meta']['id']['extracted_json'], {'old': 'retained'})
        self.assertEqual(manifest['doc_meta']['id']['latest_attempt']['content_status'], 'failed')
        service._install_file_payloads.assert_not_called()
        session.commit.assert_not_called()

    def test_persistence_failure_has_correct_stage(self):
        service, _, manifest = self.service([{'page': 1, 'text': 'available'}])
        service._install_file_payloads.side_effect = OSError('private path')
        ok, message, data = scope['parse_reference_material'](service, 'id')
        self.assertFalse(ok)
        self.assertEqual(data['code'], 'persist_failed')
        self.assertNotIn('private', message)
        self.assertEqual(manifest['doc_meta']['id']['extracted_json'], {'old': 'retained'})


if __name__ == '__main__':
    main()
