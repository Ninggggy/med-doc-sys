"""真实SQLite/临时文件故障注入，不连接真实数据库或向量服务。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.agent_backend.database.mysql.db_model import FileInfo
from agent.agent_backend.services.file_service import FileService


class DeletionRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.original = self.root / 'original.txt'
        self.parsed = self.root / 'synthetic.json'
        self.original.write_text('synthetic original')
        self.parsed.write_text('{}')
        self.engine = create_engine('sqlite:///' + str(self.root / 'test.sqlite'))
        self.addCleanup(self.engine.dispose)
        FileInfo.__table__.create(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        with self.factory() as session:
            session.add(FileInfo(doc_id='synthetic', file_name='original.txt',
                                 file_path=str(self.original), file_type='txt', create_time='2026-09-18'))
            session.commit()
        self.service = object.__new__(FileService)
        self.service.db_conn = SimpleNamespace(get_session=self.factory)
        self.service.vector_store = SimpleNamespace(delete_by_doc=Mock(return_value=1))
        self.patcher = patch('agent.agent_backend.services.file_service.PARSED_DIR', str(self.root))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def state(self):
        with self.factory() as session:
            row = session.query(FileInfo).one()
            return bool(row.is_deleted), row.index_status, row.index_error

    def test_commit_failure_keeps_original_parsed_and_vectors(self):
        session = self.factory()
        session.commit = Mock(side_effect=RuntimeError('SECRET must not appear'))
        sessions = iter([session, self.factory()])
        self.service.db_conn.get_session = lambda: next(sessions)
        ok, message = self.service.delete_file('synthetic')
        self.assertFalse(ok)
        self.assertNotIn('SECRET', message)
        self.assertFalse(self.state()[0])
        self.assertTrue(self.original.exists())
        self.assertTrue(self.parsed.exists())
        self.service.vector_store.delete_by_doc.assert_not_called()

    def test_success_commits_before_any_resource_deletion(self):
        def check_commit(doc_id):
            self.assertTrue(self.state()[0])
            return 1
        self.service.vector_store.delete_by_doc.side_effect = check_commit
        ok, _, data = self.service.delete_file_detailed('synthetic')
        self.assertTrue(ok)
        self.assertEqual(data['cleanup_state'], 'completed')
        self.assertFalse(self.original.exists())
        self.assertFalse(self.parsed.exists())
        self.assertEqual(self.state()[1], 'cleanup_completed')

    def test_vector_failure_is_visible_and_retryable_after_restart(self):
        self.service.vector_store.delete_by_doc.side_effect = RuntimeError('SECRET vector endpoint')
        ok, _, data = self.service.delete_file_detailed('synthetic')
        self.assertTrue(ok)
        self.assertEqual(data['delete_state'], 'deleted')
        self.assertEqual(data['cleanup_state'], 'pending')
        self.assertIn('vectors', data['pending_steps'])
        self.assertNotIn('SECRET', json.dumps(data) + self.state()[2])
        fresh = object.__new__(FileService)
        fresh.db_conn = self.service.db_conn
        fresh.vector_store = SimpleNamespace(delete_by_doc=Mock(return_value=0))
        pending = fresh.list_deletion_cleanups(1, 20)
        self.assertEqual(pending['total'], 1)
        self.assertEqual(pending['list'][0]['doc_id'], 'synthetic')
        self.assertEqual(fresh.delete_file_detailed('synthetic')[2]['cleanup_state'], 'completed')
        self.assertEqual(fresh.list_deletion_cleanups(1, 20)['total'], 0)

    def test_file_failure_does_not_reactivate_record(self):
        with patch('agent.agent_backend.services.file_service.os.remove', side_effect=PermissionError('SECRET path')):
            ok, _, data = self.service.delete_file_detailed('synthetic')
        self.assertTrue(ok)
        self.assertTrue(self.state()[0])
        self.assertTrue(self.original.exists())
        self.assertCountEqual(data['pending_steps'], ['source', 'parsed'])
        self.assertEqual(self.service.delete_file_detailed('synthetic')[2]['cleanup_state'], 'completed')

    def test_duplicate_delete_is_idempotent_and_not_found_is_distinct(self):
        self.assertTrue(self.service.delete_file_detailed('synthetic')[0])
        self.assertTrue(self.service.delete_file_detailed('synthetic')[0])
        ok, _, data = self.service.delete_file_detailed('unknown')
        self.assertFalse(ok)
        self.assertEqual(data['delete_state'], 'not_found')

    def test_unknown_commit_never_deletes_resources(self):
        session = self.factory()
        session.commit = Mock(side_effect=RuntimeError('commit connection lost'))
        calls = iter([session])
        self.service.db_conn.get_session = lambda: next(calls)
        ok, _, data = self.service.delete_file_detailed('synthetic')
        self.assertFalse(ok)
        self.assertEqual(data['delete_state'], 'unknown')
        self.assertTrue(self.original.exists())
        self.assertTrue(self.parsed.exists())
        self.service.vector_store.delete_by_doc.assert_not_called()

    def test_commit_ack_lost_but_persisted_can_be_confirmed(self):
        session = self.factory()
        commit = session.commit
        def commit_then_disconnect():
            commit()
            raise RuntimeError('ack lost')
        session.commit = commit_then_disconnect
        first = [session]
        self.service.db_conn.get_session = lambda: first.pop() if first else self.factory()
        ok, _, data = self.service.delete_file_detailed('synthetic')
        self.assertTrue(ok)
        self.assertEqual(data['cleanup_state'], 'completed')

    def test_pending_list_is_paginated_and_excludes_active_files(self):
        self.assertEqual(self.service.list_deletion_cleanups()['total'], 0)
        self.service.vector_store.delete_by_doc.side_effect = RuntimeError('synthetic')
        self.service.delete_file_detailed('synthetic')
        self.assertEqual(len(self.service.list_deletion_cleanups(1, 1)['list']), 1)
        self.assertEqual(self.service.list_deletion_cleanups(2, 1)['list'], [])

    def test_cleanup_status_commit_failure_remains_retryable(self):
        sessions = [self.factory(), self.factory(), self.factory()]
        sessions[2].commit = Mock(side_effect=RuntimeError('SECRET cleanup status'))
        self.service.db_conn.get_session = lambda: sessions.pop(0) if sessions else self.factory()
        ok, _, data = self.service.delete_file_detailed('synthetic')
        self.assertTrue(ok)
        self.assertEqual(data['cleanup_state'], 'pending')
        self.assertEqual(self.state()[1], 'cleanup_pending')
        self.assertFalse(self.original.exists())
        self.assertEqual(self.service.delete_file_detailed('synthetic')[2]['cleanup_state'], 'completed')

    def test_file_api_reports_partial_cleanup_and_pending_list(self):
        from flask import Flask
        from agent.agent_backend.controller import file_controller
        app = Flask('deletion-test')
        app.register_blueprint(file_controller.file_bp)
        self.service.vector_store.delete_by_doc.side_effect = RuntimeError('SECRET')
        with patch.object(file_controller, 'get_file_service', return_value=self.service), app.test_client() as client:
            response = client.post('/files/synthetic/delete')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(json.loads(response.get_data(as_text=True))['data']['cleanup_state'], 'pending')
            listing = client.post('/files/deletion-cleanups', json={'page':1,'page_size':20})
            self.assertEqual(json.loads(listing.get_data(as_text=True))['data']['total'], 1)
            self.assertEqual(client.post('/files/unknown/delete').status_code, 404)
            self.assertEqual(client.post('/files/deletion-cleanups', json={'page':'bad'}).status_code, 400)


if __name__ == '__main__':
    unittest.main()
