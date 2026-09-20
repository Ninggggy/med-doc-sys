import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.agent_backend.database.mysql.db_model import FileInfo, PharmacopeiaEntry
from agent.agent_backend.services.knowledge_source_validity import valid_source_ids, KnowledgeSourceValidationError
from agent.agent_backend.memory.rag.pipeline import RAGPipeline


class SourceValidityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.engine = create_engine('sqlite:///' + str(Path(tmp.name) / 'test.sqlite'))
        self.addCleanup(self.engine.dispose)
        FileInfo.__table__.create(self.engine)
        PharmacopeiaEntry.__table__.create(self.engine)
        self.factory = sessionmaker(bind=self.engine)
        self.connection = SimpleNamespace(get_session=self.factory)
        with self.factory() as session:
            for name, deleted in [('active', False), ('deleted', True)]:
                session.add(FileInfo(doc_id=name, file_name=name, file_path=name, file_type='txt',
                                     classification='法规', is_deleted=deleted, create_time='2026-09-18'))
            session.add(PharmacopeiaEntry(entry_id='pharm', drug_name='合成药典', affect_range='化学药',
                                         create_time=datetime.now(), update_time=datetime.now(), is_deleted=False))
            session.commit()

    def test_deleted_unknown_and_wrong_pharma_scope_are_excluded(self):
        ids=['active','deleted','unknown','pharmacopeia:化学药:pharm','pharmacopeia:中药:pharm']
        self.assertEqual(valid_source_ids(self.connection,ids), {'active','pharmacopeia:化学药:pharm'})
        self.assertEqual(valid_source_ids(self.connection,ids,{'classification':'法规'}), {'active'})
        self.assertEqual(valid_source_ids(self.connection,ids,{'classification':'药典数据'}), {'pharmacopeia:化学药:pharm'})

    def test_failure_is_explicit_and_safe(self):
        broken=SimpleNamespace(get_session=Mock(side_effect=RuntimeError('SECRET endpoint')))
        with self.assertRaises(KnowledgeSourceValidationError) as caught:
            valid_source_ids(broken,['active'])
        self.assertNotIn('SECRET',str(caught.exception))

    def pipeline(self):
        rag=object.__new__(RAGPipeline)
        rag.source_connection=self.connection
        rag.embedding=SimpleNamespace(embed=lambda text:[0.1])
        def hit(doc):
            return {'id':doc+':c1','text':'synthetic evidence','score':0.9,
                    'metadata':{'doc_id':doc,'chunk_id':'c1','classification':'法规','item_type':'chunk'}}
        rag.store=SimpleNamespace(search=lambda *a,**k:[hit('deleted'),hit('active')],
                                  keyword_search=lambda **k:[hit('deleted')],list_by_doc=lambda doc:[])
        return rag

    def test_shared_retrieval_excludes_stale_vector_and_keyword_hits(self):
        rag=self.pipeline()
        self.assertEqual([h['doc_id'] for h in rag.retrieve('synthetic')], ['active'])

    def test_database_failure_never_becomes_empty_retrieval(self):
        rag=self.pipeline()
        rag.source_connection=SimpleNamespace(get_session=Mock(side_effect=RuntimeError('SECRET')))
        with self.assertRaises(KnowledgeSourceValidationError):
            rag.retrieve('synthetic')

    def test_related_chunks_and_summary_cannot_cross_document_identity(self):
        rag = self.pipeline()
        rag.store.list_by_doc = lambda doc: [
            {'text': 'foreign evidence', 'metadata': {'doc_id': 'deleted', 'item_type': 'chunk'}},
            {'text': 'foreign summary', 'metadata': {'doc_id': 'deleted', 'item_type': 'doc_summary', 'summary': 'foreign'}},
            {'text': 'own evidence', 'metadata': {'doc_id': doc, 'item_type': 'chunk', 'chunk_id': 'c1'}},
        ]
        hit = rag.retrieve('synthetic')[0]
        self.assertEqual(hit['doc_summary'], '')
        self.assertEqual([c['text'] for c in hit['related_chunks']], ['own evidence'])

    def test_semantic_query_revalidates_cached_context(self):
        from agent.agent_backend.services.knowledge_service import KnowledgeService
        service = object.__new__(KnowledgeService)
        service.db_conn = self.connection
        stale = {'hits': [{'doc_id': 'deleted', 'text': 'stale evidence'},
                          {'doc_id': 'active', 'text': 'active evidence'}]}
        rag = SimpleNamespace(build_context=lambda **kwargs: stale)
        with patch.object(KnowledgeService, '_rag_pipeline', rag), \
             patch.object(service, '_ensure_index_for_query'):
            result = service.semantic_query('synthetic')
        self.assertEqual([h['doc_id'] for h in result['hits']], ['active'])
        self.assertEqual([h['doc_id'] for h in result['grouped_docs']], ['active'])

    def test_deleted_between_retrieval_and_context_build_is_removed(self):
        from agent.agent_backend.context.builder import ContextBuilder
        rag=self.pipeline();rag.ctx_builder=ContextBuilder()
        stale=rag.retrieve('synthetic')
        with self.factory() as session:
            session.query(FileInfo).filter_by(doc_id='active').one().is_deleted=True
            session.commit()
        with patch.object(rag,'retrieve',return_value=stale):
            data=rag.build_context('synthetic')
        self.assertEqual(data['hits'],[])
        self.assertNotIn('synthetic evidence', data['context'])


if __name__=='__main__':
    unittest.main()
