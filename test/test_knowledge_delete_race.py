"""用真实临时文件和SQLite重放后台任务与删除交错。"""
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from agent.test.test_deletion_recovery import DeletionRecoveryTests
from agent.agent_backend.services.knowledge_service import KnowledgeService


class KnowledgeDeleteRaceTests(DeletionRecoveryTests):
    def run_worker(self, callback):
        worker = object.__new__(KnowledgeService)
        worker.file_service = self.service
        worker.db_conn = self.service.db_conn
        rag = SimpleNamespace(index_file=Mock(side_effect=callback),
                              store=SimpleNamespace(has_doc=lambda doc: True,
                                                    list_by_doc=lambda doc: [{'id': 'late'}]))
        with patch.object(KnowledgeService, '_rag_pipeline', rag), \
             patch.object(KnowledgeService, '_indexed_docs', set()), \
             patch.object(KnowledgeService, '_parse_progress', {}), \
             patch('agent.agent_backend.services.knowledge_service.PARSED_DIR', str(self.root)):
            worker._run_async_parse('synthetic', str(self.original), '法规')
            progress = dict(KnowledgeService._parse_progress.get('synthetic', {}))
            self.assertNotIn('synthetic', KnowledgeService._indexed_docs)
        return rag, progress

    def late_write(self, **kwargs):
        self.service.delete_file_detailed('synthetic')
        # 模拟删除清理结束后，旧解析任务才落盘。
        self.parsed.write_text(json.dumps([{'chunk_id': 'late', 'text': 'synthetic'}]))

    def test_deleted_before_worker_does_not_start_indexing(self):
        self.service.delete_file_detailed('synthetic')
        rag, progress = self.run_worker(lambda **kwargs: None)
        rag.index_file.assert_not_called()
        self.assertNotEqual(progress.get('status'), 'completed')

    def test_deletion_during_indexing_cleans_late_artifact(self):
        _, progress = self.run_worker(self.late_write)
        self.assertFalse(self.parsed.exists())
        self.assertEqual(self.state()[1], 'cleanup_completed')
        self.assertNotEqual(progress.get('status'), 'completed')
        self.assertGreaterEqual(self.service.vector_store.delete_by_doc.call_count, 2)

    def test_failure_after_late_write_also_cleans(self):
        def fail(**kwargs):
            self.late_write(**kwargs)
            raise RuntimeError('SECRET parser detail')
        _, progress = self.run_worker(fail)
        self.assertFalse(self.parsed.exists())
        self.assertNotEqual(progress.get('status'), 'completed')

    def test_late_vector_cleanup_failure_stays_retryable(self):
        def fail_cleanup(**kwargs):
            self.late_write(**kwargs)
            self.service.vector_store.delete_by_doc.side_effect = RuntimeError('SECRET')
        _, progress = self.run_worker(fail_cleanup)
        self.assertEqual(self.state()[1], 'cleanup_pending')
        self.assertNotIn('SECRET', self.state()[2])
        self.assertNotEqual(progress.get('status'), 'completed')
