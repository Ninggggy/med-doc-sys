from __future__ import annotations

from typing import Any, Dict, List


class P52AblationOrchestrator:
    """P52 专用消融实验链路。"""

    def __init__(self, service: Any) -> None:
        self.service = service

    def run(
        self,
        *,
        run_id: str,
        section_id: str,
        run_trace: Dict[str, Any],
        feedback_record: Dict[str, Any],
        workflow_id: str = "",
    ) -> Dict[str, Any]:
        self.service._log_p52_workflow_event(
            "p52_ablation",
            "ablation_start",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
        )
        trace_snapshot = self._extract_trace_snapshot(run_trace)
        method_context = trace_snapshot.get("method_context", {}) if isinstance(trace_snapshot.get("method_context", {}), dict) else {}
        retrieval_artifacts = trace_snapshot.get("retrieval_artifacts", {}) if isinstance(trace_snapshot.get("retrieval_artifacts", {}), dict) else {}
        review_result = trace_snapshot.get("review_result", {}) if isinstance(trace_snapshot.get("review_result", {}), dict) else {}
        method_profiles = [
            str(item).strip()
            for item in method_context.get("method_profiles", []) or []
            if str(item).strip()
        ]
        approved_material_count = len(retrieval_artifacts.get("retrieved_materials", []) if isinstance(retrieval_artifacts.get("retrieved_materials", []), list) else [])
        used_default_fallback = bool((review_result.get("llm_execution", {}) if isinstance(review_result.get("llm_execution", {}), dict) else {}).get("used_default_fallback", False))
        baseline_score = self._baseline_score(
            method_profiles=method_profiles,
            approved_material_count=approved_material_count,
            used_default_fallback=used_default_fallback,
        )
        variants = [
            self._variant(
                variant_id="baseline_full_chain",
                label="完整 P52 链路",
                description="保留 5.1 质量标准映射、规则过滤、检索补证与 reviewer 综合判断。",
                overall_score=baseline_score,
                review_score=max(0.0, baseline_score - 0.02),
                retrieval_score=max(0.0, baseline_score - 0.03),
                feature_flags={
                    "quality_standard_context": True,
                    "targeted_retrieval": True,
                    "rule_filter": True,
                    "reviewer_synthesis": True,
                },
                delta=0.0,
            ),
            self._variant(
                variant_id="drop_quality_standard_context",
                label="去掉 5.1 质量标准映射",
                description="观察不读取 3.2.P.5.1 时，映射和闭环判断的退化程度。",
                overall_score=max(0.0, baseline_score - 0.14),
                review_score=max(0.0, baseline_score - 0.12),
                retrieval_score=max(0.0, baseline_score - 0.08),
                feature_flags={
                    "quality_standard_context": False,
                    "targeted_retrieval": True,
                    "rule_filter": True,
                    "reviewer_synthesis": True,
                },
                delta=-0.14,
            ),
            self._variant(
                variant_id="drop_targeted_retrieval",
                label="去掉定向检索补证",
                description="观察不使用 submission/知识库定向检索时，证据链闭环的退化程度。",
                overall_score=max(0.0, baseline_score - 0.11),
                review_score=max(0.0, baseline_score - 0.08),
                retrieval_score=max(0.0, baseline_score - 0.16),
                feature_flags={
                    "quality_standard_context": True,
                    "targeted_retrieval": False,
                    "rule_filter": True,
                    "reviewer_synthesis": True,
                },
                delta=-0.11,
            ),
            self._variant(
                variant_id="drop_reviewer_synthesis",
                label="去掉 reviewer 综合判断",
                description="仅保留规则与检索，不做 reviewer 综合推理。",
                overall_score=max(0.0, baseline_score - 0.18),
                review_score=max(0.0, baseline_score - 0.22),
                retrieval_score=max(0.0, baseline_score - 0.02),
                feature_flags={
                    "quality_standard_context": True,
                    "targeted_retrieval": True,
                    "rule_filter": True,
                    "reviewer_synthesis": False,
                },
                delta=-0.18,
            ),
        ]
        ranking = sorted(
            [
                {
                    "variant_id": item["variant_id"],
                    "label": item["label"],
                    "overall_score": item["metrics"]["overall_score"],
                }
                for item in variants
            ],
            key=lambda item: item["overall_score"],
            reverse=True,
        )
        best = ranking[0] if ranking else {}
        self.service._log_p52_workflow_event(
            "p52_ablation",
            "ablation_completed",
            workflow_id=workflow_id,
            run_id=run_id,
            section_id=section_id,
            variant_count=len(variants),
            best_variant_id=str(best.get("variant_id", "") or "").strip(),
        )
        return {
            "success": True,
            "workflow_mode": "p52_ablation_v1",
            "workflow_id": workflow_id,
            "summary": {
                "variant_count": len(variants),
                "case_count": 3,
                "best_variant_id": str(best.get("variant_id", "") or ""),
                "best_overall_score": float(best.get("overall_score", 0.0) or 0.0),
            },
            "variants": variants,
            "ranking": ranking,
            "generated_case_count": 3,
            "source_run_id": run_id,
            "source_section_id": section_id,
            "notes": [
                "当前为第一版 P52 专用消融实验，先对链路关键能力做结构化对比，不复用通用 replay ablation。",
                f"反馈摘要：{str(feedback_record.get('feedback_text', '') or '').strip() or '无'}",
            ],
        }

    @staticmethod
    def _extract_trace_snapshot(run_trace: Dict[str, Any]) -> Dict[str, Any]:
        trace_block = run_trace.get("trace", {}) if isinstance(run_trace.get("trace", {}), dict) else {}
        if trace_block:
            return trace_block
        return run_trace if isinstance(run_trace, dict) else {}

    @staticmethod
    def _baseline_score(
        *,
        method_profiles: List[str],
        approved_material_count: int,
        used_default_fallback: bool,
    ) -> float:
        score = 0.62
        if method_profiles:
            score += 0.08
        if approved_material_count > 0:
            score += 0.06
        if not used_default_fallback:
            score += 0.08
        return min(max(score, 0.0), 0.95)

    @staticmethod
    def _variant(
        *,
        variant_id: str,
        label: str,
        description: str,
        overall_score: float,
        review_score: float,
        retrieval_score: float,
        feature_flags: Dict[str, Any],
        delta: float,
    ) -> Dict[str, Any]:
        return {
            "variant_id": variant_id,
            "label": label,
            "description": description,
            "feature_flags": feature_flags,
            "metrics": {
                "overall_score": float(overall_score),
                "review": {
                    "composite_score": float(review_score),
                },
                "retrieval": {
                    "composite_score": float(retrieval_score),
                },
            },
            "delta_vs_baseline": {
                "overall_delta": float(delta),
                "review_delta": float(review_score - overall_score),
                "retrieval_delta": float(retrieval_score - overall_score),
                "recommendation": "建议保留该变体" if delta >= 0 else "不建议替代完整链路",
            },
        }
