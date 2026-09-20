from __future__ import annotations

from typing import Any, Dict, List, Tuple

from agent.agent_backend.database.mysql.db_model import PreReviewProject
from agent.agent_backend.services.p52_review_toolkit import P52ReviewToolkit


class P52ReplayVerifier:
    """执行带 patch overlay 的 P52 回放验证。"""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.toolkit = P52ReviewToolkit(service)

    def plan(
        self,
        *,
        run_id: str,
        section_id: str,
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
        feedback_record: Dict[str, Any],
        candidate_patches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        trace_snapshot = self._extract_trace_snapshot(run_trace)
        method_profiles = [
            str(item).strip()
            for item in trace_snapshot.get("method_context", {}).get("method_profiles", []) or []
            if str(item).strip()
        ]
        sibling_cases, adversarial_cases = self._collect_related_cases(
            run_id=run_id,
            section_id=section_id,
            method_profiles=method_profiles,
        )
        overlay = self.service.p52_patch_apply_service.build_runtime_overlay(
            run_id=run_id,
            section_id=section_id,
            patch_rows=candidate_patches,
            include_approved=True,
            include_candidate=True,
        )
        return {
            "overall_verdict": "pending_replay",
            "supports_auto_replay": True,
            "method_profiles": method_profiles,
            "planned_cases": {
                "current_case": {
                    "run_id": run_id,
                    "section_id": section_id,
                    "feedback_text": str(feedback_record.get("feedback_text", "") or "").strip(),
                },
                "same_profile_cases": sibling_cases,
                "adversarial_cases": adversarial_cases,
            },
            "verification_goals": [
                "当前问题是否消失或明显收敛",
                "同方法域章节是否保持稳定",
                "对抗章节是否引入新的误报或漏报",
                "reviewer 是否仍返回结构化结果",
            ],
            "overlay_summary": self.service.p52_patch_apply_service.describe_runtime_overlay(overlay),
            "notes": [
                f"本次回放将注入 {len(overlay.get('source_patch_ids', []))} 个运行时 patch overlay。",
                "执行回放时会重跑当前章节、同方法域章节和对抗章节。",
            ],
            "run_context": {
                "project_id": str(run_context.get("project_id", "") or "").strip(),
                "source_doc_id": str(run_context.get("source_doc_id", "") or "").strip(),
            },
        }

    def verify(
        self,
        *,
        run_id: str,
        section_id: str,
        run_trace: Dict[str, Any],
        run_context: Dict[str, Any],
        feedback_record: Dict[str, Any],
        candidate_patches: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        trace_snapshot = self._extract_trace_snapshot(run_trace)
        method_profiles = [
            str(item).strip()
            for item in trace_snapshot.get("method_context", {}).get("method_profiles", []) or []
            if str(item).strip()
        ]
        project_id = str(run_context.get("project_id", "") or "").strip()
        source_doc_id = str(run_context.get("source_doc_id", "") or "").strip()
        section_map = self.toolkit.load_submission_section_map(project_id=project_id, source_doc_id=source_doc_id)
        project = self._load_project(project_id)
        if project is None:
            return {
                "overall_verdict": "failed",
                "supports_auto_replay": False,
                "error_message": "project not found",
                "patches": candidate_patches,
            }
        if not section_map:
            return {
                "overall_verdict": "failed",
                "supports_auto_replay": False,
                "error_message": "submission section map not found",
                "patches": candidate_patches,
            }

        sibling_cases, adversarial_cases = self._collect_related_cases(
            run_id=run_id,
            section_id=section_id,
            method_profiles=method_profiles,
        )
        selected_same_profile_cases = sibling_cases[:2]
        selected_adversarial_cases = adversarial_cases[:2]
        overlay = self.service.p52_patch_apply_service.build_runtime_overlay(
            run_id=run_id,
            section_id=section_id,
            patch_rows=candidate_patches,
            include_approved=True,
            include_candidate=True,
        )
        run_config = {
            "p52_runtime_patch_rows": candidate_patches,
            "p52_use_approved_patches": True,
            "p52_use_candidate_patches": True,
            "p52_runtime_overlay": overlay,
        }
        rerun_cases = self._run_case_bundle(
            project=project,
            project_id=project_id,
            source_doc_id=source_doc_id,
            base_run_id=run_id,
            current_section_id=section_id,
            section_map=section_map,
            run_config=run_config,
            current_baseline_trace=run_trace,
            selected_same_profile_cases=selected_same_profile_cases,
            selected_adversarial_cases=selected_adversarial_cases,
            root_run_id=run_id,
        )

        per_patch_replay_results: List[Dict[str, Any]] = []
        for patch_row in candidate_patches:
            if not isinstance(patch_row, dict):
                continue
            patch_id = str(patch_row.get("patch_id", "") or "").strip()
            if not patch_id:
                continue
            patch_only_row = dict(patch_row)
            patch_only_row["status"] = "candidate"
            patch_overlay = self.service.p52_patch_apply_service.build_runtime_overlay(
                run_id=run_id,
                section_id=section_id,
                patch_rows=[patch_only_row],
                include_approved=False,
                include_candidate=True,
            )
            patch_run_config = {
                "p52_runtime_patch_rows": [patch_only_row],
                "p52_use_approved_patches": False,
                "p52_use_candidate_patches": True,
                "p52_runtime_overlay": patch_overlay,
            }
            patch_rerun_cases = self._run_case_bundle(
                project=project,
                project_id=project_id,
                source_doc_id=source_doc_id,
                base_run_id=f"{run_id}:patch:{patch_id}",
                current_section_id=section_id,
                section_map=section_map,
                run_config=patch_run_config,
                current_baseline_trace=run_trace,
                selected_same_profile_cases=selected_same_profile_cases,
                selected_adversarial_cases=selected_adversarial_cases,
                root_run_id=run_id,
            )
            per_patch_replay_results.append(
                {
                    "patch_id": patch_id,
                    "patch_type": str(patch_row.get("patch_type", "") or "").strip(),
                    "status": str(patch_row.get("status", "") or "").strip(),
                    "overall_verdict": self._build_overall_verdict(patch_rerun_cases),
                    "overlay_summary": self.service.p52_patch_apply_service.describe_runtime_overlay(patch_overlay),
                    "rerun_cases": patch_rerun_cases,
                }
            )

        overall_verdict = self._build_overall_verdict(rerun_cases)
        return {
            "overall_verdict": overall_verdict,
            "supports_auto_replay": True,
            "method_profiles": method_profiles,
            "feedback_text": str(feedback_record.get("feedback_text", "") or "").strip(),
            "overlay_summary": self.service.p52_patch_apply_service.describe_runtime_overlay(overlay),
            "rerun_cases": rerun_cases,
            "per_patch_replay_results": per_patch_replay_results,
            "verification_goals": [
                "当前问题是否消失或明显收敛",
                "同方法域章节是否保持稳定",
                "对抗章节是否引入新的误报或漏报",
                "reviewer 是否仍返回结构化结果",
            ],
            "patches": candidate_patches,
        }

    def _run_case_bundle(
        self,
        *,
        project: PreReviewProject,
        project_id: str,
        source_doc_id: str,
        base_run_id: str,
        current_section_id: str,
        section_map: Dict[str, Dict[str, Any]],
        run_config: Dict[str, Any],
        current_baseline_trace: Dict[str, Any],
        selected_same_profile_cases: List[Dict[str, Any]],
        selected_adversarial_cases: List[Dict[str, Any]],
        root_run_id: str,
    ) -> Dict[str, Any]:
        rerun_cases: Dict[str, Any] = {
            "current_case": self._rerun_case(
                project=project,
                project_id=project_id,
                source_doc_id=source_doc_id,
                base_run_id=base_run_id,
                case_section_id=current_section_id,
                section_map=section_map,
                run_config=run_config,
                baseline_trace=current_baseline_trace,
                case_label="current_case",
            ),
            "same_profile_cases": [],
            "adversarial_cases": [],
        }
        for item in selected_same_profile_cases:
            rerun_cases["same_profile_cases"].append(
                self._rerun_case(
                    project=project,
                    project_id=project_id,
                    source_doc_id=source_doc_id,
                    base_run_id=base_run_id,
                    case_section_id=str(item.get("section_id", "") or "").strip(),
                    section_map=section_map,
                    run_config=run_config,
                    baseline_trace=self._find_trace(run_id=root_run_id, section_id=str(item.get("section_id", "") or "").strip()),
                    case_label="same_profile_case",
                )
            )
        for item in selected_adversarial_cases:
            rerun_cases["adversarial_cases"].append(
                self._rerun_case(
                    project=project,
                    project_id=project_id,
                    source_doc_id=source_doc_id,
                    base_run_id=base_run_id,
                    case_section_id=str(item.get("section_id", "") or "").strip(),
                    section_map=section_map,
                    run_config=run_config,
                    baseline_trace=self._find_trace(run_id=root_run_id, section_id=str(item.get("section_id", "") or "").strip()),
                    case_label="adversarial_case",
                )
            )
        return rerun_cases

    @staticmethod
    def _extract_trace_snapshot(run_trace: Dict[str, Any]) -> Dict[str, Any]:
        trace_block = run_trace.get("trace", {}) if isinstance(run_trace.get("trace", {}), dict) else {}
        if trace_block:
            return trace_block
        return run_trace if isinstance(run_trace, dict) else {}

    def _collect_related_cases(
        self,
        *,
        run_id: str,
        section_id: str,
        method_profiles: List[str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        traces = self.service.get_section_traces(run_id=run_id, section_id="")
        same_profile_cases: List[Dict[str, Any]] = []
        adversarial_cases: List[Dict[str, Any]] = []
        for item in traces:
            if not isinstance(item, dict):
                continue
            current_section_id = str(item.get("section_id", "") or "").strip()
            if not current_section_id or current_section_id == section_id:
                continue
            if not current_section_id.startswith("3.2.p.5.2."):
                continue
            trace_snapshot = self._extract_trace_snapshot(item)
            current_profiles = [
                str(profile).strip()
                for profile in (
                    trace_snapshot.get("method_context", {}).get("method_profiles", [])
                    if isinstance(trace_snapshot.get("method_context", {}), dict)
                    else []
                )
                if str(profile).strip()
            ]
            payload = {
                "section_id": current_section_id,
                "section_name": str(item.get("section_name", "") or current_section_id).strip(),
                "method_profiles": current_profiles,
            }
            if method_profiles and set(current_profiles) & set(method_profiles):
                same_profile_cases.append(payload)
            else:
                adversarial_cases.append(payload)
        return same_profile_cases[:3], adversarial_cases[:3]

    def _load_project(self, project_id: str) -> PreReviewProject | None:
        session = self.service.db_conn.get_session()
        try:
            return session.query(PreReviewProject).filter(PreReviewProject.project_id == str(project_id or "").strip()).first()
        finally:
            session.close()

    def _find_trace(self, *, run_id: str, section_id: str) -> Dict[str, Any]:
        traces = self.service.get_section_traces(run_id=run_id, section_id=section_id)
        return traces[-1] if isinstance(traces, list) and traces else {}

    def _rerun_case(
        self,
        *,
        project: PreReviewProject,
        project_id: str,
        source_doc_id: str,
        base_run_id: str,
        case_section_id: str,
        section_map: Dict[str, Dict[str, Any]],
        run_config: Dict[str, Any],
        baseline_trace: Dict[str, Any],
        case_label: str,
    ) -> Dict[str, Any]:
        section_payload = self.toolkit.get_section_content(section_map, case_section_id)
        text = str(section_payload.get("content", "") or "").strip()
        if not text:
            return {
                "case_label": case_label,
                "section_id": case_section_id,
                "success": False,
                "message": "section content not found",
            }
        chunk = {
            "section_id": case_section_id,
            "section_code": str(section_payload.get("section_code", "") or case_section_id).strip(),
            "section_name": str(section_payload.get("section_name", "") or case_section_id).strip(),
            "text": text,
        }
        patched_result = self.service.p52_rule_review_orchestrator.review_single_chunk(
            project=project,
            project_id=project_id,
            run_id=f"{base_run_id}:verify:{case_section_id}",
            source_doc_id=source_doc_id,
            chunk=chunk,
            previous_section_meta={},
            run_config=run_config,
            progress_callback=None,
        )
        baseline_snapshot = self._extract_trace_snapshot(baseline_trace)
        baseline_review = baseline_snapshot.get("review_result", {}) if isinstance(baseline_snapshot.get("review_result", {}), dict) else {}
        patched_review = patched_result.get("review_result", {}) if isinstance(patched_result.get("review_result", {}), dict) else {}
        return {
            "case_label": case_label,
            "section_id": case_section_id,
            "section_name": str(chunk.get("section_name", "") or case_section_id).strip(),
            "method_profiles": baseline_snapshot.get("method_context", {}).get("method_profiles", []) if isinstance(baseline_snapshot.get("method_context", {}), dict) else [],
            "success": bool(patched_result.get("success", False)),
            "baseline": self._summarize_review_result(baseline_review),
            "patched": self._summarize_review_result(patched_review),
            "changed": self._case_changed(
                self._summarize_review_result(baseline_review),
                self._summarize_review_result(patched_review),
            ),
        }

    @staticmethod
    def _summarize_review_result(review_result: Dict[str, Any]) -> Dict[str, Any]:
        data = review_result if isinstance(review_result, dict) else {}
        task_verdicts = data.get("task_verdicts", []) if isinstance(data.get("task_verdicts", []), list) else []
        return {
            "conclusion": str(data.get("pre_review_conclusion", "") or "").strip(),
            "section_summary": str(data.get("section_summary", "") or "").strip(),
            "linked_rules": data.get("linked_rules", []) if isinstance(data.get("linked_rules", []), list) else [],
            "task_count": len(task_verdicts),
            "unsupported_count": len([item for item in task_verdicts if isinstance(item, dict) and str(item.get("status", "") or "").strip() == "unsupported"]),
            "insufficient_count": len([item for item in task_verdicts if isinstance(item, dict) and str(item.get("status", "") or "").strip() == "insufficient_information"]),
        }

    @staticmethod
    def _case_changed(baseline: Dict[str, Any], patched: Dict[str, Any]) -> bool:
        return any(
            str(baseline.get(key, "") or "") != str(patched.get(key, "") or "")
            for key in ["conclusion", "task_count", "unsupported_count", "insufficient_count", "section_summary"]
        )

    def _build_overall_verdict(self, rerun_cases: Dict[str, Any]) -> str:
        current_case = rerun_cases.get("current_case", {}) if isinstance(rerun_cases.get("current_case", {}), dict) else {}
        same_profile_cases = rerun_cases.get("same_profile_cases", []) if isinstance(rerun_cases.get("same_profile_cases", []), list) else []
        adversarial_cases = rerun_cases.get("adversarial_cases", []) if isinstance(rerun_cases.get("adversarial_cases", []), list) else []
        current_changed = bool(current_case.get("changed", False))
        adversarial_regression = any(
            isinstance(item, dict)
            and isinstance(item.get("baseline", {}), dict)
            and isinstance(item.get("patched", {}), dict)
            and int((item.get("patched", {}) or {}).get("unsupported_count", 0) or 0) > int((item.get("baseline", {}) or {}).get("unsupported_count", 0) or 0)
            for item in adversarial_cases
        )
        same_profile_all_failed = same_profile_cases and all(
            isinstance(item, dict) and not bool(item.get("changed", False))
            for item in same_profile_cases
        )
        if adversarial_regression:
            return "regressed"
        if current_changed and not same_profile_all_failed:
            return "improved"
        return "needs_review"
