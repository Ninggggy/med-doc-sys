"""检索来源的数据库有效性核对；不缓存删除/启用状态。"""
from agent.agent_backend.database.mysql.db_model import FileInfo, PharmacopeiaEntry


class KnowledgeSourceValidationError(RuntimeError):
    code = 'knowledge_source_validation_failed'

    def __init__(self):
        super().__init__('检索来源有效性核对失败，请稍后重试')


def valid_source_ids(connection, doc_ids, filters=None):
    """仅返回已知主表中的有效身份；存储故障不能降级为无命中。"""
    ids = {str(value) for value in doc_ids if value}
    if not ids:
        return set()
    filters = filters or {}
    files = sorted(value for value in ids if not value.startswith('pharmacopeia:'))
    pharm_ids = sorted({value.rsplit(':', 1)[-1] for value in ids if value.startswith('pharmacopeia:')})
    session = None
    active = set()

    def matches(row, classification=None):
        fields = ('classification', 'affect_range', 'profession_classification',
                  'registration_scope', 'registration_path', 'experience_type')
        for key in fields:
            if key not in filters or filters[key] in (None, ''):
                continue
            expected = filters[key]
            actual = classification if key == 'classification' and classification is not None else getattr(row, key, None)
            accepted = actual in expected if isinstance(expected, (list, tuple, set)) else actual == expected
            if not accepted:
                return False
        return True

    try:
        session = connection.get_session()
        for start in range(0, len(files), 400):
            rows = session.query(FileInfo).filter(FileInfo.doc_id.in_(files[start:start + 400]), FileInfo.is_deleted == False).all()
            active.update(row.doc_id for row in rows if matches(row))
        for start in range(0, len(pharm_ids), 400):
            rows = session.query(PharmacopeiaEntry).filter(PharmacopeiaEntry.entry_id.in_(pharm_ids[start:start + 400]), PharmacopeiaEntry.is_deleted == False).all()
            for row in rows:
                identity = f'pharmacopeia:{row.affect_range}:{row.entry_id}'
                if identity in ids and matches(row, classification='药典数据'):
                    active.add(identity)
        return active
    except Exception:
        raise KnowledgeSourceValidationError() from None
    finally:
        if session is not None:
            try:
                session.close()
            except Exception:
                raise KnowledgeSourceValidationError() from None
