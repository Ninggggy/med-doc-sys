"""Crash a separate deletion process after file cleanup, before vector completion."""
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from test_deletion_recovery import DeletionRecoveryTests
from agent.agent_backend.services import file_service


def crash_during_cleanup(root):
    root = Path(root).resolve()
    assert root.is_dir() and (root/'test.sqlite').is_file()
    engine = create_engine('sqlite:///' + str(root/'test.sqlite'))
    service = object.__new__(file_service.FileService)
    service.db_conn = SimpleNamespace(get_session=sessionmaker(bind=engine))
    service.vector_store = SimpleNamespace(delete_by_doc=lambda _: os._exit(23))
    file_service.PARSED_DIR = str(root)
    service.delete_file_detailed('synthetic')
    raise AssertionError('child must exit at the injected cleanup boundary')


class ProcessRestartTest(DeletionRecoveryTests):
    def test_actual_process_exit_retains_retryable_cleanup(self):
        child = subprocess.run([sys.executable, __file__, '--crash', str(self.root)],
                               capture_output=True, text=True, timeout=45)
        self.assertEqual(child.returncode, 23, child.stderr)
        self.assertTrue(self.state()[0])
        self.assertEqual(self.state()[1], 'cleanup_pending')
        self.assertFalse(self.original.exists())
        self.assertFalse(self.parsed.exists())
        pending = self.service.list_deletion_cleanups(1, 20)
        self.assertEqual(pending['total'], 1)
        ok, message, data = self.service.delete_file_detailed('synthetic')
        self.assertTrue(ok, message)
        self.assertEqual(data['cleanup_state'], 'completed')
        self.assertEqual(self.service.list_deletion_cleanups(1, 20)['total'], 0)
        self.service.vector_store.delete_by_doc.assert_called_once_with('synthetic')


if __name__ == '__main__' and len(sys.argv) == 3 and sys.argv[1] == '--crash':
    crash_during_cleanup(sys.argv[2])
