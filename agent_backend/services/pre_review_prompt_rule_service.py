from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from agent.agent_backend.config.settings import settings
from agent.agent_backend.database.mysql.db_model import PreReviewPromptRule
from agent.agent_backend.utils.file_util import ensure_dir_exists


SYSTEM_RULE_PROJECT_ID = "000000000000"

TASK_TEMPLATE_MAP = {
    "planner": "chapter_planner.j2",
    "retrieval_evaluator": "retrieval_evaluator.j2",
    "task_question": "task_question.j2",
    "reviewer": "chapter_reviewer.j2",
    "feedback_analyzer": "feedback_analyzer.j2",
    "feedback_optimizer": "feedback_optimizer.j2",
    "meta_reflector": "meta_reflector.j2",
}

PATCH_TARGET_TASK_MAP = {
    "planner": "planner",
    "retrieval_evaluator": "retrieval_evaluator",
    "task_question": "task_question",
    "reviewer": "reviewer",
    "pre_review": "reviewer",
    "feedback_analyzer": "feedback_analyzer",
    "feedback_optimizer": "feedback_optimizer",
}

DEFAULT_RULE_FILES: Dict[str, List[Dict[str, Any]]] = {
    "planner_rules.json": [
        {
            "task_type": "planner",
            "template_name": "chapter_planner.j2",
            "route_key": "pre_review/planner",
            "scope_type": "global",
            "rule_code": "planner_short_query_default",
            "rule_name": "Planner 短 Query 约束",
            "priority": 10,
            "rule_text": "生成 query_list 时，优先使用药品名称、章节主题、产品类型和关注点；禁止只输出章节编号、目录编号或章节路径片段。",
        },
        {
            "task_type": "planner",
            "template_name": "chapter_planner.j2",
            "route_key": "pre_review/planner",
            "scope_type": "global",
            "rule_code": "planner_focus_first",
            "rule_name": "Planner 关注点优先",
            "priority": 20,
            "rule_text": "当 focus_points 非空时，至少一条 query 必须直接围绕 focus_points 组织；当 focus_points 为空时，也不能复制大段原文，应围绕 section_name 生成 3 到 5 条短 query。",
        },
    ],
    "retrieval_evaluator_rules.json": [
        {
            "task_type": "retrieval_evaluator",
            "template_name": "retrieval_evaluator.j2",
            "route_key": "pre_review/retrieval_evaluator",
            "scope_type": "global",
            "rule_code": "retrieval_eval_entity_match_first",
            "rule_name": "Retrieval Evaluator entity match first",
            "priority": 10,
            "rule_text": "If a retrieved material is about a different drug or different named entity than the current section object, reject it and record entity_mismatch.",
        },
        {
            "task_type": "retrieval_evaluator",
            "template_name": "retrieval_evaluator.j2",
            "route_key": "pre_review/retrieval_evaluator",
            "scope_type": "global",
            "rule_code": "retrieval_eval_focus_and_rule_alignment",
            "rule_name": "Retrieval Evaluator align to focus points and rules",
            "priority": 20,
            "rule_text": "Approve a material only if it can directly support current focus_points, section_rules, or the section review intent. Generic but irrelevant material must be rejected.",
        },
        {
            "task_type": "retrieval_evaluator",
            "template_name": "retrieval_evaluator.j2",
            "route_key": "pre_review/retrieval_evaluator",
            "scope_type": "global",
            "rule_code": "retrieval_eval_respect_negative_feedback",
            "rule_name": "Retrieval Evaluator respect negative feedback",
            "priority": 30,
            "rule_text": "If a document or chunk was previously marked incorrect or partial in historical retrieval feedback, reject it by default unless there is explicit new evidence for keeping it.",
        },
    ],
    "task_question_rules.json": [
        {
            "task_type": "task_question",
            "template_name": "task_question.j2",
            "route_key": "pre_review/task_question",
            "scope_type": "global",
            "rule_code": "task_question_align_review_tasks",
            "rule_name": "Task Question 对齐审评任务",
            "priority": 10,
            "rule_text": "task_questions 必须逐条对应 review_tasks 中的任务对象、判断目标和比较维度，禁止退化成章节概括。",
        },
        {
            "task_type": "task_question",
            "template_name": "task_question.j2",
            "route_key": "pre_review/task_question",
            "scope_type": "global",
            "rule_code": "task_question_missing_info_explicit",
            "rule_name": "Task Question 显式缺口标注",
            "priority": 20,
            "rule_text": "若当前证据不足以支持判断，必须在 missing_information 中显式写出缺失的规则依据、事实或关键数据，不得提前输出最终合规结论。",
        },
    ],
    "reviewer_rules.json": [
        {
            "task_type": "reviewer",
            "template_name": "chapter_reviewer.j2",
            "route_key": "pre_review/reviewer",
            "scope_type": "global",
            "rule_code": "reviewer_focus_alignment",
            "rule_name": "Reviewer 关注点对齐",
            "priority": 10,
            "rule_text": "输出 supported_points、missing_points、questions 时，必须逐条对照 focus_points，不得遗漏任何已给定的章节关注点。",
        },
        {
            "task_type": "reviewer",
            "template_name": "chapter_reviewer.j2",
            "route_key": "pre_review/reviewer",
            "scope_type": "global",
            "rule_code": "reviewer_evidence_boundary",
            "rule_name": "Reviewer 证据边界",
            "priority": 20,
            "rule_text": "历史经验只能作为风险提醒，不能替代法规依据；如果 retrieved_materials 和原文都不能直接支持结论，只能输出 insufficient_information 或提出补充问题。",
        },
        {
            "task_type": "reviewer",
            "template_name": "chapter_reviewer.j2",
            "route_key": "pre_review/reviewer",
            "scope_type": "global",
            "rule_code": "reviewer_rule_alignment",
            "rule_name": "Reviewer 章节规则显式对齐",
            "priority": 30,
            "rule_text": "section_rules 中与本章节相关的约束必须被显式映射到 linked_rules、missing_points、unsupported_points 或 questions，禁止静默忽略。",
        },
    ],
    "feedback_analyzer_rules.json": [
        {
            "task_type": "feedback_analyzer",
            "template_name": "feedback_analyzer.j2",
            "route_key": "feedback/analyzer",
            "scope_type": "global",
            "rule_code": "feedback_taxonomy_fixed",
            "rule_name": "固定归因 Taxonomy",
            "priority": 10,
            "rule_text": "error_types 必须优先落在固定 taxonomy 中；如果 trace 中存在 effective_queries、source_breakdown、error_breakdown 或 retrieved_materials，应优先基于 trace 证据归因，而不是只复述用户反馈。",
        },
        {
            "task_type": "feedback_analyzer",
            "template_name": "feedback_analyzer.j2",
            "route_key": "feedback/analyzer",
            "scope_type": "global",
            "rule_code": "feedback_experience_reusable",
            "rule_name": "经验提炼可复用",
            "priority": 20,
            "rule_text": "new_experience 必须提炼成可复用规则句，并明确适用范围，不得直接复制用户原话或泛泛描述“加强判断”。",
        },
    ],
    "feedback_optimizer_rules.json": [
        {
            "task_type": "feedback_optimizer",
            "template_name": "feedback_optimizer.j2",
            "route_key": "feedback/optimizer",
            "scope_type": "global",
            "rule_code": "optimizer_min_patch_only",
            "rule_name": "最小 Patch 原则",
            "priority": 10,
            "rule_text": "只允许生成最小 patch：query 问题优先只改 planner，推理或表达问题优先只改 reviewer，不得整段重写模板。",
        },
        {
            "task_type": "feedback_optimizer",
            "template_name": "feedback_optimizer.j2",
            "route_key": "feedback/optimizer",
            "scope_type": "global",
            "rule_code": "optimizer_patch_actionable",
            "rule_name": "Patch 内容可落地",
            "priority": 20,
            "rule_text": "patch_content 必须是可直接注入规则层的约束句，包含触发条件或适用对象，禁止输出抽象口号式建议。",
        },
    ],
    "meta_reflector_rules.json": [
        {
            "task_type": "meta_reflector",
            "template_name": "meta_reflector.j2",
            "route_key": "feedback/meta_reflector",
            "scope_type": "global",
            "rule_code": "meta_bad_case_first",
            "rule_name": "元反思优先关注 Bad Case",
            "priority": 10,
            "rule_text": "当本轮结论反馈或检索反馈为 incorrect 或 partial 时，必须优先输出 bad_case=true，并总结最值得复用的纠偏经验。",
        },
        {
            "task_type": "meta_reflector",
            "template_name": "meta_reflector.j2",
            "route_key": "feedback/meta_reflector",
            "scope_type": "global",
            "rule_code": "meta_few_shot_grounded",
            "rule_name": "Few-shot 示例必须有据可依",
            "priority": 20,
            "rule_text": "few_shot_example 必须来自当前章节真实链路中的输入、证据和输出，不得生成脱离上下文的新事实或理想化答案。",
        },
    ],
}

TASK_RULE_FILE_MAP = {
    "planner": "planner_rules.json",
    "retrieval_evaluator": "retrieval_evaluator_rules.json",
    "task_question": "task_question_rules.json",
    "reviewer": "reviewer_rules.json",
    "feedback_analyzer": "feedback_analyzer_rules.json",
    "feedback_optimizer": "feedback_optimizer_rules.json",
    "meta_reflector": "meta_reflector_rules.json",
}


class PreReviewPromptRuleService:
    def __init__(self) -> None:
        self.rule_dir = Path(settings.rule_data_dir)
        ensure_dir_exists(str(self.rule_dir))
        self._ensure_seed_files()

    @staticmethod
    def _now() -> datetime:
        return datetime.now()

    @staticmethod
    def _needs_seed_repair(text: str) -> bool:
        if not text:
            return True
        mojibake_tokens = ("鐢熸垚", "鍙緭鍑", "閫愭潯", "鍏虫敞鐐", "鍙嶉", "绔犺妭")
        return any(token in text for token in mojibake_tokens)

    def _ensure_seed_files(self) -> None:
        for file_name, rules in DEFAULT_RULE_FILES.items():
            path = self.rule_dir / file_name
            if path.exists():
                try:
                    text = path.read_text(encoding="utf-8")
                    if not self._needs_seed_repair(text):
                        continue
                except Exception:
                    pass
            path.write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _load_seed_rules(self) -> List[Dict[str, Any]]:
        loaded: List[Dict[str, Any]] = []
        loaded_files = set()
        for path in sorted(self.rule_dir.glob("*.json")):
            try:
                text = path.read_text(encoding="utf-8")
                if self._needs_seed_repair(text):
                    text = json.dumps(DEFAULT_RULE_FILES.get(path.name, []), ensure_ascii=False, indent=2)
                    path.write_text(text + "\n", encoding="utf-8")
                payload = json.loads(text)
            except Exception:
                payload = DEFAULT_RULE_FILES.get(path.name, [])
            if not isinstance(payload, list):
                continue
            loaded_files.add(path.name)
            for item in payload:
                if not isinstance(item, dict):
                    continue
                loaded.append({**item, "_source_file": path.name})
        for file_name, payload in DEFAULT_RULE_FILES.items():
            if file_name in loaded_files:
                continue
            for item in payload:
                loaded.append({**item, "_source_file": file_name})
        return loaded

    @staticmethod
    def _normalize_seed_rule_item(task_type: str, item: Dict[str, Any]) -> Dict[str, Any]:
        normalized_task_type = str(task_type or "").strip()
        template_name = str(item.get("template_name", "") or TASK_TEMPLATE_MAP.get(normalized_task_type, "")).strip()
        rule_code = str(item.get("rule_code", "") or "").strip()
        rule_text = str(item.get("rule_text", "") or "").strip()
        if not normalized_task_type or not template_name or not rule_code or not rule_text:
            raise ValueError("task_type, template_name, rule_code and rule_text are required")
        return {
            "task_type": normalized_task_type,
            "template_name": template_name,
            "route_key": str(item.get("route_key", "") or f"pre_review/{normalized_task_type}").strip(),
            "scope_type": str(item.get("scope_type", "") or "global").strip() or "global",
            "rule_code": rule_code,
            "rule_name": str(item.get("rule_name", "") or rule_code).strip() or rule_code,
            "section_id": str(item.get("section_id", "") or "").strip(),
            "section_name": str(item.get("section_name", "") or "").strip(),
            "review_domain": str(item.get("review_domain", "") or "").strip(),
            "product_type": str(item.get("product_type", "") or "").strip(),
            "registration_class": str(item.get("registration_class", "") or "").strip(),
            "priority": int(item.get("priority", 100) or 100),
            "rule_text": rule_text,
            "is_active": bool(item.get("is_active", True)),
        }

    def _seed_file_for_task(self, task_type: str) -> str:
        normalized_task_type = str(task_type or "").strip()
        file_name = TASK_RULE_FILE_MAP.get(normalized_task_type, "")
        if not file_name:
            raise ValueError(f"unsupported task_type for seed rules: {task_type}")
        return file_name

    def _write_seed_rule_file(self, file_name: str, rules: List[Dict[str, Any]]) -> None:
        path = self.rule_dir / str(file_name or "").strip()
        normalized_rules = [dict(item) for item in rules if isinstance(item, dict)]
        path.write_text(json.dumps(normalized_rules, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def list_seed_rules(self, task_type: str = "") -> List[Dict[str, Any]]:
        normalized_task_type = str(task_type or "").strip()
        rows: List[Dict[str, Any]] = []
        for item in self._load_seed_rules():
            if not isinstance(item, dict):
                continue
            if normalized_task_type and str(item.get("task_type", "") or "").strip() != normalized_task_type:
                continue
            rows.append(
                {
                    "task_type": str(item.get("task_type", "") or "").strip(),
                    "template_name": str(item.get("template_name", "") or "").strip(),
                    "route_key": str(item.get("route_key", "") or "").strip(),
                    "scope_type": str(item.get("scope_type", "") or "").strip(),
                    "rule_code": str(item.get("rule_code", "") or "").strip(),
                    "rule_name": str(item.get("rule_name", "") or "").strip(),
                    "section_id": str(item.get("section_id", "") or "").strip(),
                    "section_name": str(item.get("section_name", "") or "").strip(),
                    "review_domain": str(item.get("review_domain", "") or "").strip(),
                    "product_type": str(item.get("product_type", "") or "").strip(),
                    "registration_class": str(item.get("registration_class", "") or "").strip(),
                    "priority": int(item.get("priority", 100) or 100),
                    "rule_text": str(item.get("rule_text", "") or "").strip(),
                    "is_active": bool(item.get("is_active", True)),
                    "source_file": str(item.get("_source_file", "") or "").strip(),
                }
            )
        return rows

    def upsert_seed_rule(self, task_type: str, rule_payload: Dict[str, Any]) -> Dict[str, Any]:
        normalized_task_type = str(task_type or "").strip()
        file_name = self._seed_file_for_task(normalized_task_type)
        normalized_item = self._normalize_seed_rule_item(normalized_task_type, rule_payload if isinstance(rule_payload, dict) else {})
        current_items = [
            item
            for item in self.list_seed_rules(normalized_task_type)
            if str(item.get("source_file", "") or "").strip() == file_name
        ]
        next_items: List[Dict[str, Any]] = []
        replaced = False
        for item in current_items:
            if str(item.get("rule_code", "") or "").strip() == normalized_item["rule_code"]:
                next_items.append({**normalized_item})
                replaced = True
                continue
            next_items.append(
                {
                    key: value
                    for key, value in item.items()
                    if key != "source_file"
                }
            )
        if not replaced:
            next_items.append({**normalized_item})
        next_items.sort(key=lambda item: (int(item.get("priority", 100) or 100), str(item.get("rule_code", "") or "")))
        self._write_seed_rule_file(file_name, next_items)
        return {**normalized_item, "source_file": file_name}

    def delete_seed_rule(self, task_type: str, rule_code: str) -> bool:
        normalized_task_type = str(task_type or "").strip()
        normalized_rule_code = str(rule_code or "").strip()
        if not normalized_task_type or not normalized_rule_code:
            return False
        file_name = self._seed_file_for_task(normalized_task_type)
        current_items = [
            {
                key: value
                for key, value in item.items()
                if key != "source_file"
            }
            for item in self.list_seed_rules(normalized_task_type)
            if str(item.get("source_file", "") or "").strip() == file_name
        ]
        next_items = [
            item
            for item in current_items
            if str(item.get("rule_code", "") or "").strip() != normalized_rule_code
        ]
        changed = len(next_items) != len(current_items)
        if changed:
            self._write_seed_rule_file(file_name, next_items)
        return changed

    def bootstrap_system_rules(self, session) -> None:
        now = self._now()
        seed_items = [item for item in self._load_seed_rules() if isinstance(item, dict)]
        expected_keys = {
            (
                str(item.get("task_type", "") or "").strip(),
                str(item.get("rule_code", "") or "").strip(),
            )
            for item in seed_items
            if str(item.get("task_type", "") or "").strip() and str(item.get("rule_code", "") or "").strip()
        }
        existing_seed_rows = (
            session.query(PreReviewPromptRule)
            .filter(
                PreReviewPromptRule.project_id == SYSTEM_RULE_PROJECT_ID,
                PreReviewPromptRule.source_type == "seed",
            )
            .all()
        )
        for row in existing_seed_rows:
            row_key = (
                str(getattr(row, "task_type", "") or "").strip(),
                str(getattr(row, "rule_code", "") or "").strip(),
            )
            if row_key not in expected_keys:
                session.delete(row)

        for item in seed_items:
            task_type = str(item.get("task_type", "") or "").strip()
            template_name = str(item.get("template_name", "") or TASK_TEMPLATE_MAP.get(task_type, "")).strip()
            rule_text = str(item.get("rule_text", "") or "").strip()
            rule_code = str(item.get("rule_code", "") or "").strip()
            if not task_type or not template_name or not rule_text or not rule_code:
                continue
            existing = (
                session.query(PreReviewPromptRule)
                .filter(
                    PreReviewPromptRule.project_id == SYSTEM_RULE_PROJECT_ID,
                    PreReviewPromptRule.task_type == task_type,
                    PreReviewPromptRule.rule_code == rule_code,
                )
                .first()
            )
            if existing is None:
                session.add(
                    PreReviewPromptRule(
                        rule_id=f"rule_{uuid.uuid4().hex[:16]}",
                        project_id=SYSTEM_RULE_PROJECT_ID,
                        task_type=task_type,
                        template_name=template_name,
                        route_key=str(item.get("route_key", "") or "").strip(),
                        scope_type=str(item.get("scope_type", "") or "global").strip() or "global",
                        rule_code=rule_code,
                        rule_name=str(item.get("rule_name", "") or task_type).strip(),
                        section_id=str(item.get("section_id", "") or "").strip() or None,
                        section_name=str(item.get("section_name", "") or "").strip() or None,
                        review_domain=str(item.get("review_domain", "") or "").strip() or None,
                        product_type=str(item.get("product_type", "") or "").strip() or None,
                        registration_class=str(item.get("registration_class", "") or "").strip() or None,
                        priority=int(item.get("priority", 100) or 100),
                        rule_text=rule_text,
                        source_type="seed",
                        is_active=bool(item.get("is_active", True)),
                        payload_json=json.dumps(item, ensure_ascii=False),
                        create_time=now,
                        update_time=now,
                    )
                )
                continue
            existing.template_name = template_name
            existing.route_key = str(item.get("route_key", "") or "").strip()
            existing.scope_type = str(item.get("scope_type", "") or "global").strip() or "global"
            existing.rule_name = str(item.get("rule_name", "") or task_type).strip()
            existing.section_id = str(item.get("section_id", "") or "").strip() or None
            existing.section_name = str(item.get("section_name", "") or "").strip() or None
            existing.review_domain = str(item.get("review_domain", "") or "").strip() or None
            existing.product_type = str(item.get("product_type", "") or "").strip() or None
            existing.registration_class = str(item.get("registration_class", "") or "").strip() or None
            existing.priority = int(item.get("priority", 100) or 100)
            existing.rule_text = rule_text
            existing.is_active = bool(item.get("is_active", True))
            existing.payload_json = json.dumps(item, ensure_ascii=False)
            existing.update_time = now

    def ensure_project_rules(self, session, project_id: str) -> None:
        self.bootstrap_system_rules(session)
        existing_rows = (
            session.query(PreReviewPromptRule)
            .filter(
                PreReviewPromptRule.project_id == project_id,
                PreReviewPromptRule.source_type == "project_copy",
            )
            .all()
        )
        system_rules = (
            session.query(PreReviewPromptRule)
            .filter(PreReviewPromptRule.project_id == SYSTEM_RULE_PROJECT_ID)
            .order_by(PreReviewPromptRule.priority.asc(), PreReviewPromptRule.id.asc())
            .all()
        )
        now = self._now()
        existing_by_code = {
            (
                str(getattr(item, "task_type", "") or "").strip(),
                str(getattr(item, "rule_code", "") or "").strip(),
            ): item
            for item in existing_rows
        }
        expected_keys = {
            (
                str(getattr(item, "task_type", "") or "").strip(),
                str(getattr(item, "rule_code", "") or "").strip(),
            )
            for item in system_rules
            if str(getattr(item, "task_type", "") or "").strip() and str(getattr(item, "rule_code", "") or "").strip()
        }
        for item in existing_rows:
            item_key = (
                str(getattr(item, "task_type", "") or "").strip(),
                str(getattr(item, "rule_code", "") or "").strip(),
            )
            if item_key not in expected_keys:
                session.delete(item)
        for item in system_rules:
            item_key = (
                str(getattr(item, "task_type", "") or "").strip(),
                str(getattr(item, "rule_code", "") or "").strip(),
            )
            row = existing_by_code.get(item_key)
            if row is None:
                session.add(
                    PreReviewPromptRule(
                        rule_id=f"rule_{uuid.uuid4().hex[:16]}",
                        project_id=project_id,
                        task_type=item.task_type,
                        template_name=item.template_name,
                        route_key=item.route_key,
                        scope_type="project" if item.scope_type == "global" else item.scope_type,
                        rule_code=item.rule_code,
                        rule_name=item.rule_name,
                        section_id=item.section_id,
                        section_name=item.section_name,
                        review_domain=item.review_domain,
                        product_type=item.product_type,
                        registration_class=item.registration_class,
                        priority=item.priority,
                        rule_text=item.rule_text,
                        source_type="project_copy",
                        is_active=bool(item.is_active),
                        payload_json=item.payload_json,
                        create_time=now,
                        update_time=now,
                    )
                )
                continue
            row.task_type = item.task_type
            row.template_name = item.template_name
            row.route_key = item.route_key
            row.scope_type = "project" if item.scope_type == "global" else item.scope_type
            row.rule_name = item.rule_name
            row.section_id = item.section_id
            row.section_name = item.section_name
            row.review_domain = item.review_domain
            row.product_type = item.product_type
            row.registration_class = item.registration_class
            row.priority = item.priority
            row.rule_text = item.rule_text
            row.source_type = "project_copy"
            row.is_active = bool(item.is_active)
            row.payload_json = item.payload_json
            row.update_time = now

    @staticmethod
    def _scope_matches(
        rule: PreReviewPromptRule,
        section_id: str,
        section_name: str,
        review_domain: str,
        product_type: str,
        registration_class: str,
    ) -> bool:
        if rule.section_id and str(rule.section_id).strip() not in {"", "all"}:
            target = str(rule.section_id).strip()
            if section_id != target and not section_id.startswith(f"{target}."):
                return False
        if rule.section_name and str(rule.section_name).strip() not in {"", "all"}:
            target = str(rule.section_name).strip()
            if target not in section_name:
                return False
        if rule.review_domain and str(rule.review_domain).strip() not in {"", "all"}:
            if str(rule.review_domain).strip() != review_domain:
                return False
        if rule.product_type and str(rule.product_type).strip() not in {"", "all"}:
            if str(rule.product_type).strip() != product_type:
                return False
        if rule.registration_class and str(rule.registration_class).strip() not in {"", "all"}:
            if str(rule.registration_class).strip() not in registration_class:
                return False
        return True

    def resolve_rules(
        self,
        session,
        project_id: str,
        task_type: str,
        section_id: str = "",
        section_name: str = "",
        review_domain: str = "",
        product_type: str = "",
        registration_class: str = "",
    ) -> List[PreReviewPromptRule]:
        rows = (
            session.query(PreReviewPromptRule)
            .filter(
                PreReviewPromptRule.project_id == project_id,
                PreReviewPromptRule.is_active == 1,
                PreReviewPromptRule.task_type.in_([task_type, "all"]),
            )
            .order_by(PreReviewPromptRule.priority.asc(), PreReviewPromptRule.id.asc())
            .all()
        )
        return [
            row
            for row in rows
            if self._scope_matches(
                row,
                section_id=str(section_id or "").strip(),
                section_name=str(section_name or "").strip(),
                review_domain=str(review_domain or "").strip(),
                product_type=str(product_type or "").strip(),
                registration_class=str(registration_class or "").strip(),
            )
        ]

    def compose_prompt_config(
        self,
        session,
        project_id: str,
        base_prompt_config: Dict[str, Any] | None,
        task_type: str,
        section_id: str = "",
        section_name: str = "",
        review_domain: str = "",
        product_type: str = "",
        registration_class: str = "",
    ) -> Dict[str, Any]:
        self.ensure_project_rules(session, project_id)
        prompt_config = dict(base_prompt_config or {})
        bundle = prompt_config.get("prompt_bundle", {}) if isinstance(prompt_config.get("prompt_bundle", {}), dict) else {}
        bundle = dict(bundle)
        template_suffixes = bundle.get("template_suffixes", {}) if isinstance(bundle.get("template_suffixes", {}), dict) else {}
        template_suffixes = dict(template_suffixes)
        active_rules = bundle.get("active_rules", {}) if isinstance(bundle.get("active_rules", {}), dict) else {}
        active_rules = dict(active_rules)

        matched = self.resolve_rules(
            session=session,
            project_id=project_id,
            task_type=task_type,
            section_id=section_id,
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            registration_class=registration_class,
        )
        template_name = TASK_TEMPLATE_MAP.get(task_type, "")
        suffix_lines = [str(item.rule_text).strip() for item in matched if str(item.rule_text or "").strip()]
        if template_name and suffix_lines:
            existing = str(template_suffixes.get(template_name, "") or "").strip()
            template_suffixes[template_name] = "\n".join([x for x in [existing, *suffix_lines] if x]).strip()
        active_rules[task_type] = [
            {
                "rule_id": str(item.rule_id or ""),
                "rule_code": str(item.rule_code or ""),
                "rule_name": str(item.rule_name or ""),
                "template_name": str(item.template_name or ""),
                "priority": int(item.priority or 0),
                "scope_type": str(item.scope_type or ""),
                "source_type": str(item.source_type or ""),
            }
            for item in matched
        ]
        bundle["template_suffixes"] = template_suffixes
        bundle["active_rules"] = active_rules
        prompt_config["prompt_bundle"] = bundle
        return prompt_config

    def sync_patch_rules(
        self,
        session,
        project_id: str,
        section_id: str,
        review_domain: str,
        product_type: str,
        registration_class: str,
        source_feedback_key: str,
        patches: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        now = self._now()
        synced: List[Dict[str, Any]] = []
        for patch in patches or []:
            if not isinstance(patch, dict):
                continue
            patch_id = str(patch.get("patch_id", "") or "").strip()
            patch_content = str(patch.get("patch_content", "") or "").strip()
            if not patch_id or not patch_content:
                continue
            raw_target_agent = str(patch.get("target_agent", "") or "").strip()
            task_type = PATCH_TARGET_TASK_MAP.get(raw_target_agent, "")
            template_name = TASK_TEMPLATE_MAP.get(task_type, "")
            if not task_type or not template_name:
                continue
            patch_type = str(patch.get("patch_type", "") or "").strip() or "reasoning_patch"
            target_scope = str(patch.get("target_scope", "") or section_id).strip() or section_id
            scope_type = "section" if target_scope.startswith("3.2.") else "project"
            rule_code = f"patch_{patch_id}"
            rule_name = f"Patch {patch_type} {raw_target_agent or task_type}"
            payload = {
                "patch_id": patch_id,
                "patch_type": patch_type,
                "target_agent": raw_target_agent,
                "normalized_task_type": task_type,
                "target_scope": target_scope,
                "trigger_condition": str(patch.get("trigger_condition", "") or "").strip(),
                "source_feedback_key": source_feedback_key,
            }
            row = (
                session.query(PreReviewPromptRule)
                .filter(
                    PreReviewPromptRule.project_id == project_id,
                    PreReviewPromptRule.rule_code == rule_code,
                )
                .first()
            )
            if row is None:
                row = PreReviewPromptRule(
                    rule_id=f"rule_{uuid.uuid4().hex[:16]}",
                    project_id=project_id,
                    task_type=task_type,
                    template_name=template_name,
                    route_key=f"feedback_patch/{task_type}",
                    scope_type=scope_type,
                    rule_code=rule_code,
                    rule_name=rule_name,
                    section_id=target_scope if scope_type == "section" else None,
                    section_name=None,
                    review_domain=review_domain or None,
                    product_type=product_type or None,
                    registration_class=registration_class or None,
                    priority=5,
                    rule_text=patch_content,
                    source_type="patch",
                    is_active=True,
                    payload_json=json.dumps(payload, ensure_ascii=False),
                    create_time=now,
                    update_time=now,
                )
                session.add(row)
            else:
                row.task_type = task_type
                row.template_name = template_name
                row.route_key = f"feedback_patch/{task_type}"
                row.scope_type = scope_type
                row.rule_name = rule_name
                row.section_id = target_scope if scope_type == "section" else None
                row.review_domain = review_domain or None
                row.product_type = product_type or None
                row.registration_class = registration_class or None
                row.priority = 5
                row.rule_text = patch_content
                row.source_type = "patch"
                row.is_active = True
                row.payload_json = json.dumps(payload, ensure_ascii=False)
                row.update_time = now
            synced.append(
                {
                    "rule_code": rule_code,
                    "task_type": task_type,
                    "template_name": template_name,
                    "scope_type": scope_type,
                    "section_id": target_scope if scope_type == "section" else "",
                    "source_feedback_key": source_feedback_key,
                }
            )
        return synced
