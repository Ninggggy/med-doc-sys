import json
import uuid
from datetime import datetime
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

from agent.agent_backend.database.mysql.db_model import FilingChangeReviewRule


class FilingChangeRuleService:
    VALID_RISK_LEVELS = {"低", "中", "高", "需人工确认", "low", "medium", "high", "manual"}

    def __init__(self, db_conn: Any) -> None:
        self.db_conn = db_conn

    @staticmethod
    def _now() -> datetime:
        return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _safe_json(value: Any, default: Any) -> Any:
        if isinstance(value, (dict, list)):
            return value
        if value in (None, ""):
            return default
        try:
            return json.loads(str(value))
        except Exception:
            return default

    def create_rule(self, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        rule_code = str(payload.get("rule_code", "")).strip()
        rule_name = str(payload.get("rule_name", "")).strip()
        rule_content = str(payload.get("rule_content", "")).strip()
        if not (rule_code and rule_name and rule_content):
            return False, "rule_code/rule_name/rule_content is required", None
        drug_category = str(payload.get("drug_category", "")).strip()
        task_type = str(payload.get("task_type", "extend_validity_period")).strip() or "extend_validity_period"
        if not drug_category:
            return False, "drug_category is required", None
        risk_level = str(payload.get("risk_level", "")).strip()
        if risk_level and risk_level not in self.VALID_RISK_LEVELS:
            return False, "invalid risk_level", None
        rule_json_payload = payload.get("rule_json", {}) if isinstance(payload.get("rule_json", {}), dict) else {}
        rule_json_payload.update(
            {
                "rule_category": str(payload.get("rule_category", "")).strip(),
                "drug_category": drug_category,
                "applicable_change_item": str(payload.get("applicable_change_item", "")).strip(),
                "applicable_material_category": str(payload.get("applicable_material_category", "")).strip(),
                "applicable_fields": payload.get("applicable_fields", []),
                "rule_condition": payload.get("rule_condition", {}),
                "hit_result": str(payload.get("hit_result", "")).strip(),
                "risk_level": risk_level,
                "basis_source": str(payload.get("basis_source", "")).strip(),
                "remark": str(payload.get("remark", "")).strip(),
            }
        )
        from agent.agent_backend.services.filing_change_rule_review_service import validate_rule
        error = validate_rule({**payload, 'rule_json': rule_json_payload})
        if error: return False, error, None
        now = self._now()
        row = FilingChangeReviewRule(
            rule_id=f"fcrule_{uuid.uuid4().hex[:14]}",
            rule_code=rule_code,
            rule_name=rule_name,
            rule_type=str(payload.get("rule_type", "technical")).strip() or "technical",
            task_type=task_type,
            rule_content=rule_content,
            rule_json=self._json(rule_json_payload),
            evidence_source=str(payload.get("evidence_source", "")).strip(),
            enabled=bool(payload.get("enabled", True)),
            created_at=now,
            updated_at=now,
        )
        session = self.db_conn.get_session()
        try:
            session.add(row)
            session.commit()
            return True, "success", self._serialize(row)
        except Exception as exc:
            session.rollback()
            return False, str(exc), None
        finally:
            session.close()

    def list_rules(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        task_type = str(payload.get("task_type", "")).strip()
        enabled = payload.get("enabled", None)
        rule_type = str(payload.get("rule_type", "")).strip()
        rule_category = str(payload.get("rule_category", "")).strip()
        drug_category = str(payload.get("drug_category", "")).strip()
        session = self.db_conn.get_session()
        try:
            query = session.query(FilingChangeReviewRule)
            if task_type:
                query = query.filter(FilingChangeReviewRule.task_type == task_type)
            if enabled is not None:
                query = query.filter(FilingChangeReviewRule.enabled == bool(enabled))
            if rule_type:
                query = query.filter(FilingChangeReviewRule.rule_type == rule_type)
            rows = query.order_by(FilingChangeReviewRule.updated_at.desc()).all()
            data = [self._serialize(r) for r in rows]
            if rule_category:
                data = [x for x in data if str((x.get("rule_json", {}) or {}).get("rule_category", "")) == rule_category]
            if drug_category:
                data = [x for x in data if str((x.get("rule_json", {}) or {}).get("drug_category", "")) == drug_category]
            return {"list": data, "total": len(data)}
        finally:
            session.close()

    def update_rule(self, rule_id: str, payload: Dict[str, Any]) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        session = self.db_conn.get_session()
        try:
            row = session.query(FilingChangeReviewRule).filter(FilingChangeReviewRule.rule_id == rule_id).first()
            if row is None:
                return False, "rule not found", None
            if "rule_name" in payload:
                row.rule_name = str(payload.get("rule_name", "")).strip() or row.rule_name
            if "rule_type" in payload:
                row.rule_type = str(payload.get("rule_type", "")).strip() or row.rule_type
            if "rule_content" in payload:
                row.rule_content = str(payload.get("rule_content", "")).strip() or row.rule_content
            if "rule_json" in payload:
                old = self._safe_json(row.rule_json, {})
                new_json = payload.get("rule_json", {})
                if isinstance(new_json, dict):
                    old.update(new_json)
                row.rule_json = self._json(old)
            if "evidence_source" in payload:
                row.evidence_source = str(payload.get("evidence_source", "")).strip()
            if "enabled" in payload:
                row.enabled = bool(payload.get("enabled"))
            if any(k in payload for k in ["rule_category", "drug_category", "applicable_change_item", "applicable_material_category", "applicable_fields", "rule_condition", "hit_result", "risk_level", "basis_source", "remark"]):
                old = self._safe_json(row.rule_json, {})
                for key in ["rule_category", "drug_category", "applicable_change_item", "applicable_material_category", "applicable_fields", "rule_condition", "hit_result", "risk_level", "basis_source", "remark"]:
                    if key in payload:
                        old[key] = payload.get(key)
                row.rule_json = self._json(old)
            if 'task_type' in payload:
                row.task_type = str(payload['task_type'])
            from agent.agent_backend.services.filing_change_rule_review_service import validate_rule
            error = validate_rule(self._serialize(row))
            if error:
                session.rollback()
                return False, error, None
            row.updated_at = self._now()
            session.commit()
            return True, "success", self._serialize(row)
        except Exception as exc:
            session.rollback()
            return False, str(exc), None
        finally:
            session.close()

    def delete_rule(self, rule_id: str) -> Tuple[bool, str]:
        session = self.db_conn.get_session()
        try:
            row = session.query(FilingChangeReviewRule).filter(FilingChangeReviewRule.rule_id == rule_id).first()
            if row is None:
                return False, "rule not found"
            session.delete(row)
            session.commit()
            return True, "success"
        except Exception as exc:
            session.rollback()
            return False, str(exc)
        finally:
            session.close()

    def import_rules(self, upload_file) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        if upload_file is None:
            return False, "file is required", None
        try:
            payload = json.loads(upload_file.read().decode("utf-8", errors="ignore"))
        except Exception as exc:
            return False, f"invalid json: {exc}", None
        items = payload if isinstance(payload, list) else payload.get("rules", [])
        if not isinstance(items, list):
            return False, "rules must be list", None
        created = 0
        errors = []
        exist_codes = {x.get("rule_code", "") for x in self.list_rules({}).get("list", [])}
        for item in items:
            if not isinstance(item, dict):
                continue
            normalized = dict(item)
            # 兼容外部规则模板字段命名
            if not str(normalized.get("rule_content", "")).strip():
                normalized["rule_content"] = str(normalized.get("condition", "")).strip()
            if "hit_output" in normalized and "hit_result" not in normalized:
                normalized["hit_result"] = normalized.get("hit_output", "")
            if "condition" in normalized and "rule_condition" not in normalized:
                normalized["rule_condition"] = normalized.get("condition", "")
            code = str(item.get("rule_code", "")).strip()
            if not code:
                errors.append({"rule_code": "", "error": "rule_code is required"})
                continue
            if code in exist_codes:
                errors.append({"rule_code": code, "error": "duplicate rule_code"})
                continue
            ok, msg, _ = self.create_rule(normalized)
            if ok:
                created += 1
                exist_codes.add(code)
            else:
                errors.append({"rule_code": code, "error": msg})
        if created == 0 and len(errors) > 0:
            return False, "import failed", {"created_count": created, "failed_count": len(errors), "errors": errors}
        if len(errors) > 0:
            return True, "partial success", {"created_count": created, "failed_count": len(errors), "errors": errors}
        return True, "success", {"created_count": created, "failed_count": 0, "errors": []}

    def _serialize(self, row: FilingChangeReviewRule) -> Dict[str, Any]:
        return {
            "rule_id": row.rule_id,
            "rule_code": row.rule_code,
            "rule_name": row.rule_name,
            "rule_type": row.rule_type,
            "task_type": row.task_type,
            "rule_content": row.rule_content,
            "rule_json": self._safe_json(row.rule_json, {}),
            "evidence_source": row.evidence_source or "",
            "enabled": bool(row.enabled),
            "created_at": row.created_at.strftime("%Y-%m-%d %H:%M:%S") if row.created_at else "",
            "updated_at": row.updated_at.strftime("%Y-%m-%d %H:%M:%S") if row.updated_at else "",
        }
