import json
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, or_

from agent.agent_backend.database.mysql.db_model import PharmacopeiaEntry
from agent.agent_backend.database.mysql.mysql_conn import MysqlConnection
from agent.agent_backend.memory.rag.pipeline import RAGPipeline


FIELD_MAP = {
    "处方": "prescription",
    "性状": "character",
    "鉴别": "identification",
    "检查": "inspection",
    "检査": "inspection_variant",
    "含量测定": "assay",
    "类别": "category",
    "贮藏": "storage",
    "规格": "specification",
    "有关物质": "related_substances",
    "效价测定": "potency_assay",
    "活性成分含量": "active_ingredient_content",
    "制法": "preparation_method",
    "制法要求": "preparation_requirement",
    "制剂": "dosage_form",
    "制成": "prepared_into",
    "生产要求": "production_requirement",
    "标注": "labeling",
    "其他": "other",
}


class PharmacopeiaService:
    def __init__(self):
        self.db_conn = MysqlConnection()
        self.rag = RAGPipeline()

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _stringify(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value).strip()

    def _to_dict(self, row: PharmacopeiaEntry) -> Dict[str, Any]:
        index_status, indexed_count = self._get_entry_index_state(row)
        return {
            "entry_id": row.entry_id,
            "drug_name": row.drug_name,
            "affect_range": row.affect_range,
            "prescription": row.prescription or "",
            "character": row.character or "",
            "identification": row.identification or "",
            "inspection": row.inspection or "",
            "inspection_variant": row.inspection_variant or "",
            "assay": row.assay or "",
            "category": row.category or "",
            "storage": row.storage or "",
            "specification": row.specification or "",
            "related_substances": row.related_substances or "",
            "potency_assay": row.potency_assay or "",
            "active_ingredient_content": row.active_ingredient_content or "",
            "preparation_method": row.preparation_method or "",
            "preparation_requirement": row.preparation_requirement or "",
            "dosage_form": row.dosage_form or "",
            "prepared_into": row.prepared_into or "",
            "production_requirement": row.production_requirement or "",
            "labeling": row.labeling or "",
            "other": row.other or "",
            "source_file_name": row.source_file_name or "",
            "raw_payload": json.loads(row.raw_payload) if row.raw_payload else {},
            "index_status": index_status,
            "indexed_count": indexed_count,
            "create_time": row.create_time.strftime("%Y-%m-%d %H:%M:%S") if row.create_time else "",
            "update_time": row.update_time.strftime("%Y-%m-%d %H:%M:%S") if row.update_time else "",
        }

    def _get_entry_index_state(self, row: PharmacopeiaEntry) -> Tuple[str, int]:
        try:
            vector_rows = self.rag.store.list_by_doc(self._index_doc_id(row))
            indexed_count = len(vector_rows)
            return ("completed" if indexed_count > 0 else "pending", indexed_count)
        except Exception:
            return ("unknown", 0)

    @staticmethod
    def _chunk_text(text: str, max_chars: int = 1200, overlap: int = 120) -> List[str]:
        value = str(text or "").strip()
        if not value:
            return []
        if len(value) <= max_chars:
            return [value]
        paragraphs = [part.strip() for part in value.split("\n\n") if part.strip()]
        chunks: List[str] = []
        current = ""
        for para in paragraphs:
            candidate = para if not current else f"{current}\n\n{para}"
            if len(candidate) <= max_chars:
                current = candidate
                continue
            if current:
                chunks.append(current)
                if overlap > 0 and len(current) > overlap:
                    current = f"{current[-overlap:].strip()}\n\n{para}".strip()
                else:
                    current = para
                if len(current) <= max_chars:
                    continue
            while len(para) > max_chars:
                split_at = max_chars
                window = para[:max_chars]
                for sep in ("\n", "。", "；", ";", "，", ",", " "):
                    pos = window.rfind(sep)
                    if pos >= max(1, int(max_chars * 0.6)):
                        split_at = pos + 1
                        break
                piece = para[:split_at].strip()
                if piece:
                    chunks.append(piece)
                para = para[max(1, split_at - overlap) :].strip()
            current = para
        if current:
            chunks.append(current)
        return [chunk for chunk in chunks if chunk]

    @staticmethod
    def _index_doc_id(row: PharmacopeiaEntry) -> str:
        return f"pharmacopeia:{row.affect_range}:{row.entry_id}"

    @staticmethod
    def _index_title(row: PharmacopeiaEntry) -> str:
        return f"{row.drug_name} 药典数据".strip()

    def _build_index_rows(self, row: PharmacopeiaEntry) -> List[Dict[str, Any]]:
        title = self._index_title(row)
        base_path = [str(row.affect_range or "").strip(), str(row.drug_name or "").strip()]
        field_rows: List[Tuple[str, str, str]] = [
            ("基本信息", "药品名称", str(row.drug_name or "").strip()),
            ("处方", "处方", str(row.prescription or "").strip()),
            ("性状", "性状", str(row.character or "").strip()),
            ("鉴别", "鉴别", str(row.identification or "").strip()),
            ("检查", "检查", str(row.inspection or "").strip()),
            ("检查补充", "检查补充", str(row.inspection_variant or "").strip()),
            ("含量测定", "含量测定", str(row.assay or "").strip()),
            ("类别", "类别", str(row.category or "").strip()),
            ("贮藏", "贮藏", str(row.storage or "").strip()),
            ("规格", "规格", str(row.specification or "").strip()),
            ("有关物质", "有关物质", str(row.related_substances or "").strip()),
            ("效价测定", "效价测定", str(row.potency_assay or "").strip()),
            ("活性成分含量", "活性成分含量", str(row.active_ingredient_content or "").strip()),
            ("制法", "制法", str(row.preparation_method or "").strip()),
            ("制剂要求", "制剂要求", str(row.preparation_requirement or "").strip()),
            ("剂型", "剂型", str(row.dosage_form or "").strip()),
            ("制成", "制成", str(row.prepared_into or "").strip()),
            ("生产要求", "生产要求", str(row.production_requirement or "").strip()),
            ("标签", "标签", str(row.labeling or "").strip()),
            ("其他", "其他", str(row.other or "").strip()),
        ]

        parsed_rows: List[Dict[str, Any]] = [
            {
                "chunk_id": "__doc_summary__",
                "item_type": "doc_summary",
                "doc_id": self._index_doc_id(row),
                "doc_title": title,
                "doc_type": "pharmacopeia_json",
                "summary": self._build_retrieval_text(row),
                "keywords": [str(row.drug_name or "").strip(), str(row.affect_range or "").strip()],
                "text": self._build_retrieval_text(row),
            }
        ]
        chunk_no = 0
        for section_id, section_name, field_value in field_rows:
            if not field_value:
                continue
            section_path = [part for part in (base_path + [section_name]) if part]
            heading = "\n".join([f"# {base_path[-1]}"] + ([f"## {section_name}"] if section_name else []))
            chunk_source = f"{heading}\n\n{field_value}".strip()
            for idx, chunk_text in enumerate(self._chunk_text(chunk_source), start=1):
                chunk_no += 1
                parsed_rows.append(
                    {
                        "chunk_id": f"pharm_{chunk_no}_{idx}",
                        "doc_id": self._index_doc_id(row),
                        "doc_title": title,
                        "doc_type": "pharmacopeia_json",
                        "text": chunk_text,
                        "summary": section_name,
                        "keywords": [str(row.drug_name or "").strip(), section_name, str(row.affect_range or "").strip()],
                        "section_id": section_id,
                        "section_name": section_name,
                        "section_path": section_path,
                        "section_path_text": ">".join(section_path),
                        "unit_type": "pharmacopeia_entry_chunk",
                        "source_chunk_ids": [row.entry_id],
                        "char_count": len(chunk_text),
                    }
                )
        return parsed_rows

    def _sync_entry_index(self, row: PharmacopeiaEntry, force: bool = True) -> int:
        doc_id = self._index_doc_id(row)
        parsed_rows = self._build_index_rows(row)
        indexed_count = self.rag.index_preparsed(
            doc_id=doc_id,
            parsed_rows=parsed_rows,
            classification="药典数据",
            force=force,
        )
        return indexed_count

    def _delete_entry_index(self, row: PharmacopeiaEntry) -> int:
        deleted = self.rag.store.delete_by_doc(self._index_doc_id(row))
        return deleted

    def _semantic_search_entries(
        self,
        query: str,
        affect_range: str = "",
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        hits = self.rag.retrieve(
            query=query,
            top_k=max(1, int(top_k)),
            alpha=0.75,
            min_score=0.0,
            filters={"classification": "药典数据"},
        )
        entry_ids: List[str] = []
        hit_map: Dict[str, Dict[str, Any]] = {}
        for hit in hits:
            doc_id = str(hit.get("doc_id", "") or "")
            parts = doc_id.split(":")
            if len(parts) != 3 or parts[0] != "pharmacopeia":
                continue
            hit_affect_range = parts[1]
            entry_id = parts[2]
            if affect_range and hit_affect_range != affect_range:
                continue
            if entry_id not in hit_map:
                hit_map[entry_id] = hit
                entry_ids.append(entry_id)
        if not entry_ids:
            return []

        session = self.db_conn.get_session()
        try:
            rows = (
                session.query(PharmacopeiaEntry)
                .filter(
                    and_(
                        PharmacopeiaEntry.entry_id.in_(entry_ids),
                        PharmacopeiaEntry.is_deleted == 0,
                    )
                )
                .all()
            )
            row_map = {str(row.entry_id): row for row in rows}
            results: List[Dict[str, Any]] = []
            for entry_id in entry_ids:
                row = row_map.get(entry_id)
                if row is None:
                    continue
                hit = hit_map.get(entry_id) or {}
                results.append(
                    {
                        **self._to_dict(row),
                        "score": float(hit.get("score", 0.0)),
                        "vector_score": float(hit.get("vector_score", 0.0)),
                        "lexical_score": float(hit.get("lexical_score", 0.0)),
                        "retrieval_text": self._build_retrieval_text(row),
                        "retrieval_source": "milvus_semantic",
                    }
                )
            return results
        finally:
            session.close()

    @staticmethod
    def _tokenize_query(query: str) -> List[str]:
        parts = re.split(r"[\s,，。；;:：/|()（）\[\]【】]+", str(query or "").strip())
        seen = set()
        out: List[str] = []
        for part in parts:
            token = str(part or "").strip()
            if len(token) < 2 or token in seen:
                continue
            seen.add(token)
            out.append(token)
        return out

    @staticmethod
    def _build_retrieval_text(row: PharmacopeiaEntry) -> str:
        blocks = [
            f"药品名称：{row.drug_name}",
            f"作用范围：{row.affect_range}",
        ]
        for label, field_name in [
            ("处方", "prescription"),
            ("性状", "character"),
            ("鉴别", "identification"),
            ("检查", "inspection"),
            ("检查补充", "inspection_variant"),
            ("含量测定", "assay"),
            ("类别", "category"),
            ("贮藏", "storage"),
            ("规格", "specification"),
            ("有关物质", "related_substances"),
            ("效价测定", "potency_assay"),
            ("活性成分含量", "active_ingredient_content"),
            ("制法", "preparation_method"),
            ("制法要求", "preparation_requirement"),
            ("剂型", "dosage_form"),
            ("制成", "prepared_into"),
            ("生产要求", "production_requirement"),
            ("标示", "labeling"),
            ("其他", "other"),
        ]:
            value = str(getattr(row, field_name, "") or "").strip()
            if value:
                blocks.append(f"{label}：{value}")
        return "\n".join(blocks).strip()

    def search_entries(
        self,
        query: str,
        affect_range: str = "",
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        tokens = self._tokenize_query(query)
        semantic_hits = self._semantic_search_entries(query=query, affect_range=affect_range, top_k=max(3, int(top_k)))
        session = self.db_conn.get_session()
        try:
            orm_query = session.query(PharmacopeiaEntry).filter(PharmacopeiaEntry.is_deleted == 0)
            if affect_range:
                orm_query = orm_query.filter(PharmacopeiaEntry.affect_range == affect_range)
            if tokens:
                like_filters = []
                for token in tokens[:8]:
                    like = f"%{token}%"
                    like_filters.extend(
                        [
                            PharmacopeiaEntry.drug_name.like(like),
                            PharmacopeiaEntry.category.like(like),
                            PharmacopeiaEntry.specification.like(like),
                            PharmacopeiaEntry.dosage_form.like(like),
                            PharmacopeiaEntry.prepared_into.like(like),
                            PharmacopeiaEntry.other.like(like),
                            PharmacopeiaEntry.raw_payload.like(like),
                        ]
                    )
                orm_query = orm_query.filter(or_(*like_filters))
            rows = (
                orm_query.order_by(PharmacopeiaEntry.update_time.desc(), PharmacopeiaEntry.id.desc())
                .limit(max(20, top_k * 8))
                .all()
            )
            scored: List[Dict[str, Any]] = []
            for row in rows:
                retrieval_text = self._build_retrieval_text(row)
                haystack = retrieval_text.lower()
                score = 0
                for token in tokens:
                    token_lower = token.lower()
                    if token_lower in str(row.drug_name or "").lower():
                        score += 5
                    if token_lower in haystack:
                        score += 1
                if not tokens:
                    score = 1
                if score <= 0:
                    continue
                scored.append(
                    {
                        **self._to_dict(row),
                        "score": float(score),
                        "vector_score": 0.0,
                        "lexical_score": float(score),
                        "retrieval_text": retrieval_text,
                        "retrieval_source": "sql_structured",
                    }
                )
            merged: Dict[str, Dict[str, Any]] = {}
            for item in scored:
                merged[str(item.get("entry_id", ""))] = dict(item)
            for item in semantic_hits:
                entry_id = str(item.get("entry_id", "") or "")
                if not entry_id:
                    continue
                current = merged.get(entry_id)
                if current is None:
                    merged[entry_id] = dict(item)
                    continue
                current["score"] = max(float(current.get("score", 0.0)), float(item.get("score", 0.0)))
                current["vector_score"] = max(float(current.get("vector_score", 0.0)), float(item.get("vector_score", 0.0)))
                current["lexical_score"] = max(float(current.get("lexical_score", 0.0)), float(item.get("lexical_score", 0.0)))
                current["retrieval_source"] = "hybrid"
            results = list(merged.values())
            results.sort(
                key=lambda item: (
                    -float(item.get("score", 0.0)),
                    -int(item.get("indexed_count", 0) or 0),
                    str(item.get("drug_name", "")),
                )
            )
            return results[: max(1, int(top_k))]
        finally:
            session.close()

    def _fill_entity(
        self,
        row: PharmacopeiaEntry,
        drug_name: str,
        affect_range: str,
        payload: Dict[str, Any],
        source_file_name: str = "",
    ) -> None:
        row.drug_name = drug_name.strip()
        row.affect_range = affect_range.strip() or "化学药"
        for src_key, dst_key in FIELD_MAP.items():
            setattr(row, dst_key, self._stringify(payload.get(src_key)))
        row.raw_payload = json.dumps(payload, ensure_ascii=False)
        row.source_file_name = source_file_name.strip() or row.source_file_name
        row.update_time = self._now()

    def list_entries(
        self,
        keyword: str = "",
        affect_range: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        session = self.db_conn.get_session()
        try:
            query = session.query(PharmacopeiaEntry).filter(PharmacopeiaEntry.is_deleted == 0)
            if affect_range:
                query = query.filter(PharmacopeiaEntry.affect_range == affect_range)
            if keyword:
                query = query.filter(PharmacopeiaEntry.drug_name.like(f"%{keyword.strip()}%"))
            total = query.count()
            page = max(1, int(page))
            page_size = max(1, int(page_size))
            rows = (
                query.order_by(PharmacopeiaEntry.update_time.desc(), PharmacopeiaEntry.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size)
                .all()
            )
            return {
                "list": [self._to_dict(row) for row in rows],
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            }
        finally:
            session.close()

    def create_entry(self, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        drug_name = str(payload.get("drug_name", "")).strip()
        affect_range = str(payload.get("affect_range", "")).strip() or "化学药"
        if not drug_name:
            return False, "drug_name is required", None
        session = self.db_conn.get_session()
        try:
            exists = (
                session.query(PharmacopeiaEntry)
                .filter(
                    and_(
                        PharmacopeiaEntry.drug_name == drug_name,
                        PharmacopeiaEntry.affect_range == affect_range,
                        PharmacopeiaEntry.is_deleted == 0,
                    )
                )
                .first()
            )
            if exists is not None:
                return False, "entry already exists", None
            now = self._now()
            row = PharmacopeiaEntry(
                entry_id=uuid.uuid4().hex[:16],
                drug_name=drug_name,
                affect_range=affect_range,
                create_time=now,
                update_time=now,
                is_deleted=0,
            )
            self._fill_entity(row, drug_name, affect_range, payload, source_file_name=str(payload.get("source_file_name", "") or ""))
            session.add(row)
            session.commit()
            try:
                self._sync_entry_index(row, force=True)
                return True, "created", self._to_dict(row)
            except Exception as exc:
                data = self._to_dict(row)
                data["index_warning"] = str(exc)
                return True, f"created with index warning: {exc}", data
        except Exception as exc:
            session.rollback()
            return False, f"create failed: {exc}", None
        finally:
            session.close()

    def update_entry(self, entry_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PharmacopeiaEntry)
                .filter(and_(PharmacopeiaEntry.entry_id == entry_id, PharmacopeiaEntry.is_deleted == 0))
                .first()
            )
            if row is None:
                return False, "entry not found", None
            drug_name = str(payload.get("drug_name", row.drug_name)).strip() or row.drug_name
            affect_range = str(payload.get("affect_range", row.affect_range)).strip() or row.affect_range
            self._fill_entity(row, drug_name, affect_range, payload, source_file_name=str(payload.get("source_file_name", row.source_file_name or "") or ""))
            session.commit()
            try:
                self._sync_entry_index(row, force=True)
                return True, "updated", self._to_dict(row)
            except Exception as exc:
                data = self._to_dict(row)
                data["index_warning"] = str(exc)
                return True, f"updated with index warning: {exc}", data
        except Exception as exc:
            session.rollback()
            return False, f"update failed: {exc}", None
        finally:
            session.close()

    def delete_entry(self, entry_id: str) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            row = (
                session.query(PharmacopeiaEntry)
                .filter(and_(PharmacopeiaEntry.entry_id == entry_id, PharmacopeiaEntry.is_deleted == 0))
                .first()
            )
            if row is None:
                return False, "entry not found"
            row.is_deleted = 1
            row.update_time = self._now()
            session.commit()
            try:
                self._delete_entry_index(row)
                return True, "deleted"
            except Exception as exc:
                return True, f"deleted with index warning: {exc}"
        except Exception as exc:
            session.rollback()
            return False, f"delete failed: {exc}"
        finally:
            session.close()

    def import_json_file(
        self,
        file_obj,
        affect_range: str,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if file_obj is None or not getattr(file_obj, "filename", ""):
            return False, "file is required", None
        try:
            payload = json.load(file_obj.stream)
            file_obj.stream.seek(0)
        except Exception as exc:
            return False, f"invalid json file: {exc}", None
        if not isinstance(payload, dict):
            return False, "json root must be an object", None

        session = self.db_conn.get_session()
        created = 0
        updated = 0
        indexed = 0
        failed: List[Dict[str, str]] = []
        try:
            now = self._now()
            indexed_rows: List[PharmacopeiaEntry] = []
            for drug_name, raw_fields in payload.items():
                drug_name = str(drug_name or "").strip()
                if not drug_name:
                    continue
                if not isinstance(raw_fields, dict):
                    raw_fields = {"其他": self._stringify(raw_fields)}
                normalized = dict(raw_fields)
                if "检查" not in normalized and "检査" in normalized:
                    normalized["检查"] = normalized.get("检査")
                row = (
                    session.query(PharmacopeiaEntry)
                    .filter(
                        and_(
                            PharmacopeiaEntry.drug_name == drug_name,
                            PharmacopeiaEntry.affect_range == affect_range,
                        )
                    )
                    .first()
                )
                try:
                    if row is None:
                        row = PharmacopeiaEntry(
                            entry_id=uuid.uuid4().hex[:16],
                            drug_name=drug_name,
                            affect_range=affect_range,
                            create_time=now,
                            update_time=now,
                            is_deleted=0,
                        )
                        self._fill_entity(row, drug_name, affect_range, normalized, source_file_name=file_obj.filename)
                        session.add(row)
                        created += 1
                    else:
                        row.is_deleted = 0
                        self._fill_entity(row, drug_name, affect_range, normalized, source_file_name=file_obj.filename)
                        updated += 1
                    indexed_rows.append(row)
                except Exception as exc:
                    failed.append({"drug_name": drug_name, "message": str(exc)})
            session.commit()
            for row in indexed_rows:
                try:
                    indexed += self._sync_entry_index(row, force=True)
                except Exception as exc:
                    failed.append({"drug_name": row.drug_name, "message": f"index failed: {exc}"})
            return True, "import completed", {
                "created_count": created,
                "updated_count": updated,
                "indexed_count": indexed,
                "failed_count": len(failed),
                "failed": failed,
            }
        except Exception as exc:
            session.rollback()
            return False, f"import failed: {exc}", None
        finally:
            session.close()
