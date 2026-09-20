from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from agent.agent_backend.infrastructure.repositories.pre_review_repository import (
    SectionConclusionRecord,
    SectionTraceRecord,
)
from agent.agent_backend.services.pre_review_agent_contracts import PreReviewAgentContractBuilder
from agent.agent_backend.utils.agent_logging import log_agent_flow

if TYPE_CHECKING:
    from agent.agent_backend.database.mysql.db_model import PreReviewProject
    from agent.agent_backend.services.pre_review_service import PreReviewService


class SectionReviewOrchestrator:
    """Coordinate one section-level review/replay flow."""

    def __init__(self, service: "PreReviewService") -> None:
        self.service = service

    @staticmethod
    def _dedupe_texts(values: List[Any]) -> List[str]:
        out: List[str] = []
        seen = set()
        for item in values or []:
            text = str(item or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            out.append(text)
        return out

    def _build_review_checkpoints(
        self,
        *,
        section_name: str,
        focus_points: List[str],
        section_rules: List[str],
        historical_experience: List[Dict[str, Any]],
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        checkpoints: List[str] = []
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        if section_name:
            checkpoints.append(f"围绕章节“{section_name}”识别申报资料中可能存在的问题，而不是只做摘要。")
        chapter_role = str(profile.get("chapter_role", "") or "").strip()
        core_question = str(profile.get("core_review_question", "") or "").strip()
        if chapter_role:
            checkpoints.append(f"章节角色：{chapter_role}")
        if core_question:
            checkpoints.append(f"核心审评问题：{core_question}")
        for item in profile.get("reviewer_mindset", []) or []:
            text = str(item or "").strip()
            if text:
                checkpoints.append(f"审评思路：{text}")
        for item in focus_points or []:
            text = str(item or "").strip()
            if text:
                checkpoints.append(f"重点核查：{text}")
        for item in section_rules or []:
            text = str(item or "").strip()
            if text:
                checkpoints.append(f"规则约束：{text}")
        for item in historical_experience or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("content", "") or item.get("memory_text", "") or "").strip()
            if text:
                checkpoints.append(f"历史经验提醒：{text}")
        for item in profile.get("must_answer_questions", []) or []:
            text = str(item or "").strip()
            if text:
                checkpoints.append(f"必须回答：{text}")
        checkpoints.append("若发现问题，需要明确问题位置、违反的规则或指导原则、证据依据和修订建议。")
        checkpoints.append("若未发现问题，需要概括说明材料如何满足关键要求，不得照搬规则原文。")
        return self._dedupe_texts(checkpoints)[:12]

    def _build_knowledge_priority(self, *, review_domain: str, product_type: str) -> List[str]:
        priorities = [
            "国家药监法规、注册管理办法与技术指导原则",
            "ICH 指南及国际通行技术要求",
            "药典标准、检验要求及质量控制依据",
            "系统内激活的章节规则、Prompt 规则与 seed 规则",
            "项目知识库中的指导原则、法律法规、历史经验与审评案例",
        ]
        if str(review_domain or "").strip():
            priorities.insert(0, f"优先检索与“{str(review_domain).strip()}”直接相关的法规和指导原则")
        if str(product_type or "").strip():
            priorities.insert(1, f"结合“{str(product_type).strip()}”对应的剂型、工艺和质量风险要求")
        return self._dedupe_texts(priorities)

    def _build_task_definition(
        self,
        *,
        section_name: str,
        review_domain: str,
        product_type: str,
        focus_points: List[str],
        section_rules: List[str],
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        review_goal = f"围绕章节“{section_name or '当前章节'}”判断申报资料是否满足药品预审要求，并输出可执行的审评结论。"
        entity_goal = "先抽取章节中的医学关键实体，再判断这些实体是否被原文、证据和规则充分支撑。"
        data_goal = "对所有支持结论的关键数据、限度、单位、时间点、批次和统计结果做显式核对。"
        if str(review_domain or "").strip():
            review_goal = f"围绕“{str(review_domain).strip()}”域要求，判断章节“{section_name or '当前章节'}”是否满足申报标准。"
        if str(product_type or "").strip():
            entity_goal += f" 重点覆盖“{str(product_type).strip()}”对应的对象、样品、剂型、成分和关键质量属性。"
        review_goal = str(profile.get("core_review_question", "") or profile.get("review_goal", "") or review_goal).strip() or review_goal
        return {
            "review_goal": review_goal,
            "entity_extraction_goal": entity_goal,
            "data_extraction_goal": data_goal,
            "decision_policy": "只有当医学关键实体、关键数据和证据链能相互印证时，才能给出 supported 结论。",
            "output_template": "problem_basis_advice_v1",
            "priority_checks": self._dedupe_texts(list(focus_points or []) + list(section_rules or []))[:8],
            "chapter_role": str(profile.get("chapter_role", "") or "").strip(),
            "core_review_question": str(profile.get("core_review_question", "") or "").strip(),
            "must_answer_questions": self._dedupe_texts(profile.get("must_answer_questions", []) or [])[:8],
            "reasoning_principles": self._dedupe_texts(profile.get("core_review_principles", []) or [])[:8],
            "common_risks": self._dedupe_texts(profile.get("common_risks", []) or [])[:8],
            "evidence_focus": self._dedupe_texts(profile.get("evidence_focus", []) or [])[:8],
            "reviewer_mindset": self._dedupe_texts(profile.get("reviewer_mindset", []) or [])[:6],
            "task_generation_rules": self._dedupe_texts(profile.get("task_generation_rules", []) or [])[:6],
            "evidence_judgment_rules": self._dedupe_texts(profile.get("evidence_judgment_rules", []) or [])[:6],
            "conclusion_style_rules": self._dedupe_texts(profile.get("conclusion_style_rules", []) or [])[:6],
            "feedback_optimization_focus": self._dedupe_texts(profile.get("feedback_optimization_focus", []) or [])[:6],
            "section_review_profile": dict(profile),
        }

    def _build_compliance_targets(
        self,
        *,
        section_name: str,
        focus_points: List[str],
        section_rules: List[str],
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        targets: List[str] = []
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        if section_name:
            targets.append(f"确认章节“{section_name}”是否提供了完成申报所需的关键资料、数据、结论和依据。")
        for item in profile.get("must_answer_questions", []) or []:
            text = str(item or "").strip()
            if text:
                targets.append(text)
        for item in focus_points or []:
            text = str(item or "").strip()
            if text:
                targets.append(f"核对资料是否就“{text}”给出直接支撑。")
        for item in section_rules or []:
            text = str(item or "").strip()
            if text:
                targets.append(f"核对资料是否满足规则要求：{text}")
        targets.append("核对章节结论是否由原文或检索证据直接支撑，而不是只有概括性描述。")
        return self._dedupe_texts(targets)[:10]

    def _build_issue_hypotheses(
        self,
        *,
        focus_points: List[str],
        section_rules: List[str],
        historical_experience: List[Dict[str, Any]],
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        hypotheses: List[str] = [
            "是否缺少与章节结论直接对应的原始资料、研究结果、方法说明或法规依据。",
            "是否存在名称、规格、方法、限度、样品信息或结论前后不一致。",
            "是否存在仅复述规则要求、但没有给出证明材料的情况。",
        ]
        for item in profile.get("common_risks", []) or []:
            text = str(item or "").strip()
            if text:
                hypotheses.append(text)
        for item in focus_points or []:
            text = str(item or "").strip()
            if text:
                hypotheses.append(f"是否缺少与“{text}”直接对应的研究资料、方法、结果或论证。")
        for item in section_rules or []:
            text = str(item or "").strip()
            if text:
                hypotheses.append(f"是否未能满足规则“{text}”要求的提交内容或证据。")
        for item in historical_experience or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get('content', '') or item.get('memory_text', '') or '').strip()
            if text:
                hypotheses.append(f"结合历史经验重点排查：{text}")
        return self._dedupe_texts(hypotheses)[:12]

    def _build_evidence_requirements(
        self,
        *,
        review_domain: str,
        focus_points: List[str],
        product_type: str,
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        requirements: List[str] = [
            "优先寻找直接适用的法律法规、指导原则、ICH 或药典条款原文。",
            "优先寻找能直接证明满足或不满足要求的原始研究数据、表格、图谱、方法学或稳定性结果。",
            "优先寻找可直接定位到章节原文的事实依据，而不是二手转述。",
        ]
        for item in profile.get("evidence_focus", []) or []:
            text = str(item or "").strip()
            if text:
                requirements.append(text)
        if str(review_domain or "").strip():
            requirements.append(f"需要与“{str(review_domain).strip()}”直接相关的法规和技术要求依据。")
        if str(product_type or "").strip():
            requirements.append(f"需要覆盖“{str(product_type).strip()}”对应的工艺、质量属性和风险控制证据。")
        for item in focus_points or []:
            text = str(item or "").strip()
            if text:
                requirements.append(f"需要能直接证明“{text}”的研究、数据、图谱、表格或论证结论。")
        return self._dedupe_texts(requirements)[:10]

    def _build_output_requirements(self) -> List[str]:
        return self._dedupe_texts(
            [
                "若发现问题，必须形成“问题-依据-证据-建议”的闭环输出。",
                "若未发现问题，必须说明材料满足关键要求的事实依据，不能照搬规则原文。",
                "unsupported_points、missing_points、questions 必须与 rule_findings 一致。",
                "只引用本次审评结论真正使用到的规则、法规、指导原则或 ICH 依据。",
                "结论只能是 supported、unsupported 或 insufficient_information 三者之一。",
            ]
        )

    def _build_medical_entity_requirements(
        self,
        *,
        section_name: str,
        review_domain: str,
        product_type: str,
        focus_points: List[str],
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        context_text = " ".join([section_name or "", review_domain or "", product_type or "", " ".join(focus_points or [])]).lower()
        requirements: List[str] = [
            "药品名称、通用名、活性成分或指标成分名称",
            "研究对象、样品、批次、对照品或参比制剂信息",
            "剂型、规格、给药途径、包装或关键物料名称",
            "方法、仪器、分析对象、杂质或质量属性名称",
        ]
        if any(keyword in context_text for keyword in ["临床", "clinical", "有效", "安全", "药理", "毒理"]):
            requirements.extend(
                [
                    "适应症、受试人群、分组、给药方案、比较对象名称",
                    "关键疗效终点、安全性事件、药代参数名称",
                ]
            )
        else:
            requirements.extend(
                [
                    "原料药/制剂对象、晶型/盐型/辅料/杂质名称",
                    "质量标准项目、检验项目、方法学对象名称",
                ]
            )
        if any(keyword in context_text for keyword in ["稳定", "stability"]):
            requirements.append("稳定性条件、时间点、样品批次和包装系统名称")
        if any(keyword in context_text for keyword in ["杂质", "impurity"]):
            requirements.append("杂质名称、降解产物名称、对照品名称")
        if any(keyword in context_text for keyword in ["方法", "检验", "分析", "鉴定", "assay", "dissolution"]):
            requirements.append("检验方法、分析对象、系统适用性项目名称")
        for item in focus_points or []:
            text = str(item or "").strip()
            if text:
                requirements.append(f"与“{text}”直接相关的对象、成分、样品、方法或对照品名称")
        requirements.extend(self._dedupe_texts(profile.get("medical_entity_focus", []) or []))
        return self._dedupe_texts(requirements)[:12]

    def _build_medical_data_requirements(
        self,
        *,
        section_name: str,
        review_domain: str,
        product_type: str,
        focus_points: List[str],
        section_review_profile: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        profile = section_review_profile if isinstance(section_review_profile, dict) else {}
        context_text = " ".join([section_name or "", review_domain or "", product_type or "", " ".join(focus_points or [])]).lower()
        requirements: List[str] = [
            "所有与结论直接相关的定量结果、限度、单位、比较方向和对应样品/批次信息",
            "关键研究条件、时间点、方法学参数、验收标准和统计口径",
            "能支持结论的原始数值、表格结果、图谱参数或指标范围",
        ]
        if any(keyword in context_text for keyword in ["稳定", "stability"]):
            requirements.extend(
                [
                    "稳定性条件、时间点、考察项目、结果变化趋势和接受标准",
                    "批次间稳定性数据是否一致以及失效/储存条件",
                ]
            )
        if any(keyword in context_text for keyword in ["杂质", "impurity"]):
            requirements.extend(
                [
                    "杂质含量、阈值、鉴定/定量限度及单位",
                    "降解产物趋势、批次差异和控制限度",
                ]
            )
        if any(keyword in context_text for keyword in ["方法", "检验", "分析", "鉴定", "assay", "dissolution"]):
            requirements.extend(
                [
                    "方法学精密度、准确度、线性、专属性、检测限/定量限等验证数据",
                    "关键色谱条件、系统适用性结果、保留时间或峰面积等参数",
                ]
            )
        if any(keyword in context_text for keyword in ["临床", "clinical", "有效", "安全"]):
            requirements.extend(
                [
                    "样本量、疗效终点结果、安全性事件发生率和统计显著性",
                    "暴露量、剂量、疗程、PK 参数及其单位",
                ]
            )
        for item in focus_points or []:
            text = str(item or "").strip()
            if text:
                requirements.append(f"与“{text}”直接相关的结果值、限度、单位、时间点或比较结论")
        requirements.extend(self._dedupe_texts(profile.get("medical_data_focus", []) or []))
        return self._dedupe_texts(requirements)[:14]

    def review_single_chunk(
        self,
        project: "PreReviewProject",
        project_id: str,
        run_id: str,
        source_doc_id: str,
        chunk: Dict[str, Any],
        previous_section_meta: Optional[Dict[str, Any]] = None,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Dict[str, Any]:
        if not hasattr(self.service, "_resolve_pre_review_mode"):
            return self.service._review_single_chunk_impl(
                project=project,
                project_id=project_id,
                run_id=run_id,
                source_doc_id=source_doc_id,
                chunk=chunk,
                previous_section_meta=previous_section_meta,
                run_config=run_config,
                progress_callback=progress_callback,
            )
        previous_section_meta = previous_section_meta or {}
        workflow_mode = self.service._resolve_pre_review_mode(run_config)
        section_id = str(chunk.get("section_id") or chunk.get("chunk_id", ""))
        section_code = str(chunk.get("section_code") or section_id)
        section_name = str(chunk.get("section_name") or chunk.get("title") or section_code or f"section-{section_id}")
        text = str(chunk.get("text", "")).strip()
        if not text:
            return {"success": False, "message": "empty section text", "section_id": section_id}

        self.service._emit_progress(
            progress_callback,
            "section_start",
            f"开始处理章节 {section_code} {section_name}".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            unit_order=chunk.get("unit_order"),
        )
        paragraph_blocks = self.service._build_paragraph_anchors(text=text, section_id=section_id, section_code=section_code)
        base_prompt_config = (run_config or {}).get("prompt_config", {}) if isinstance((run_config or {}).get("prompt_config", {}), dict) else {}
        feature_flags = self.service.runtime_context_service.resolve_feature_flags(
            run_config=run_config if isinstance(run_config, dict) else {},
            base_prompt_config=base_prompt_config,
        )
        base_prompt_config = {**base_prompt_config, "feature_flags": feature_flags}
        runtime_context = self.service.runtime_context_service.build_section_review_context(
            project=project,
            project_id=project_id,
            section_id=section_id,
            section_name=section_name,
            chunk=chunk if isinstance(chunk, dict) else {},
            run_config=run_config if isinstance(run_config, dict) else {},
        )
        registration_class = runtime_context.registration_class
        review_domain = runtime_context.review_domain
        product_type = runtime_context.product_type
        focus_points = runtime_context.focus_points
        section_rules = runtime_context.section_rules
        historical_experience = runtime_context.historical_experience
        historical_bad_retrievals = runtime_context.historical_bad_retrievals
        reference_examples = runtime_context.reference_examples
        section_review_profile = runtime_context.section_review_profile
        review_checkpoints = self._build_review_checkpoints(
            section_name=section_name,
            focus_points=focus_points,
            section_rules=section_rules,
            historical_experience=historical_experience,
            section_review_profile=section_review_profile,
        )
        knowledge_priority = self._build_knowledge_priority(
            review_domain=review_domain,
            product_type=product_type,
        )
        compliance_targets = self._build_compliance_targets(
            section_name=section_name,
            focus_points=focus_points,
            section_rules=section_rules,
            section_review_profile=section_review_profile,
        )
        issue_hypotheses = self._build_issue_hypotheses(
            focus_points=focus_points,
            section_rules=section_rules,
            historical_experience=historical_experience,
            section_review_profile=section_review_profile,
        )
        evidence_requirements = self._build_evidence_requirements(
            review_domain=review_domain,
            focus_points=focus_points,
            product_type=product_type,
            section_review_profile=section_review_profile,
        )
        output_requirements = self._build_output_requirements()
        task_definition = self._build_task_definition(
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            focus_points=focus_points,
            section_rules=section_rules,
            section_review_profile=section_review_profile,
        )
        medical_entity_requirements = self._build_medical_entity_requirements(
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            focus_points=focus_points,
            section_review_profile=section_review_profile,
        )
        medical_data_requirements = self._build_medical_data_requirements(
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            focus_points=focus_points,
            section_review_profile=section_review_profile,
        )
        runtime_context.task_definition = dict(task_definition)
        runtime_context.compliance_targets = list(compliance_targets)
        runtime_context.issue_hypotheses = list(issue_hypotheses)
        runtime_context.evidence_requirements = list(evidence_requirements)
        runtime_context.output_requirements = list(output_requirements)
        runtime_context.medical_entity_requirements = list(medical_entity_requirements)
        runtime_context.medical_data_requirements = list(medical_data_requirements)

        planner_contract = PreReviewAgentContractBuilder.planner_input(
            task_id=f"{run_id}:{section_id}",
            application_id=project_id,
            section_id=section_id,
            section_name=section_name,
            registration_class=registration_class,
            review_domain=review_domain,
            product_type=product_type,
            raw_text=self.service._compact_text(f"{section_name} {text}".strip(), max_len=240),
            focus_points=focus_points,
            section_rules=section_rules,
            review_checkpoints=review_checkpoints,
            knowledge_priority=knowledge_priority,
            task_definition=task_definition,
            section_review_profile=section_review_profile,
            compliance_targets=compliance_targets,
            issue_hypotheses=issue_hypotheses,
            evidence_requirements=evidence_requirements,
            output_requirements=output_requirements,
            medical_entity_requirements=medical_entity_requirements,
            medical_data_requirements=medical_data_requirements,
            reference_examples=reference_examples,
            historical_experience=historical_experience,
            metadata={
                "source_file_ids": [
                    str(item.get("doc_id", "") or "").strip()
                    for item in chunk.get("attached_files", [])
                    if isinstance(item, dict)
                ],
                "language": "zh-CN",
            },
        )
        planner_input = planner_contract.to_dict()
        planner_prompt_config = self.service._compose_runtime_prompt_config(
            project_id=project_id,
            task_type="planner",
            base_prompt_config=base_prompt_config,
            section_id=section_id,
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            registration_class=registration_class,
        )
        self.service._emit_progress(
            progress_callback,
            "planner_start",
            f"开始规划章节 {section_code} 的检索计划".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            focus_points=focus_points,
        )
        planner_result_raw = self.service.planner_reviewer_agent.plan(planner_input, prompt_config=planner_prompt_config)
        planner_result = self.service._normalize_planner_result(
            planner_result=planner_result_raw if isinstance(planner_result_raw, dict) else {},
            section_name=section_name,
            product_type=product_type,
            registration_class=registration_class,
            focus_points=focus_points,
        )
        log_agent_flow(
            "planner",
            "done",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            query_count=len(self.service._normalize_text_list(planner_result.get("query_list", []))),
            sources=self.service._normalize_text_list(
                [item.get("source_type", "") for item in planner_result.get("retrieval_plan", []) if isinstance(item, dict)]
            ),
            missing_info_flags=self.service._normalize_text_list(planner_result.get("missing_info_flags", [])),
        )
        self.service._emit_progress(
            progress_callback,
            "planner_done",
            f"章节 {section_code} 检索计划已生成".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            query_list=self.service._normalize_text_list(planner_result.get("query_list", [])),
            retrieval_plan=planner_result.get("retrieval_plan", []),
        )

        self.service._emit_progress(
            progress_callback,
            "retrieval_start",
            f"开始检索章节 {section_code} 相关资料".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
        )
        retrieved_materials = self.service._execute_retrieval_plan(
            planner_result,
            historical_experience,
            review_domain=review_domain,
            focus_points=focus_points,
            section_id=section_id,
            section_name=section_name,
            progress_callback=progress_callback,
        )
        log_agent_flow(
            "retrieval",
            "done",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            raw_hit_count=len(retrieved_materials),
            sources=self.service._summarize_source_breakdown(retrieved_materials),
        )
        self.service._emit_progress(
            progress_callback,
            "retrieval_done",
            f"章节 {section_code} 检索完成".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            hit_count=len(retrieved_materials),
            source_types=self.service._normalize_text_list([item.get("source_type", "") for item in retrieved_materials if isinstance(item, dict)]),
        )

        retrieval_evaluator_input = PreReviewAgentContractBuilder.retrieval_evaluator_input(
            planner_contract,
            raw_text=text,
            retrieved_materials=retrieved_materials,
            historical_bad_retrievals=historical_bad_retrievals,
            review_tasks=planner_result.get("review_tasks", []),
            retrieval_blueprint=planner_result.get("retrieval_blueprint", {}),
            entity_extraction_focus=planner_result.get("entity_extraction_focus", []),
            data_extraction_focus=planner_result.get("data_extraction_focus", []),
            reference_examples=reference_examples,
            historical_experience=historical_experience,
            section_rules=section_rules,
        ).to_dict()
        retrieval_evaluator_prompt_config = self.service._compose_runtime_prompt_config(
            project_id=project_id,
            task_type="retrieval_evaluator",
            base_prompt_config=base_prompt_config,
            section_id=section_id,
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            registration_class=registration_class,
        )
        self.service._emit_progress(
            progress_callback,
            "retrieval_evaluator_start",
            f"开始评估章节 {section_code} 检索资料质量".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            raw_hit_count=len(retrieved_materials),
        )
        if feature_flags.get("use_retrieval_evaluator", True):
            retrieval_evaluation_result = self.service.retrieval_evaluator_agent.evaluate(
                retrieval_evaluator_input,
                prompt_config=retrieval_evaluator_prompt_config,
            )
            retrieval_evaluation_result = self.service._normalize_retrieval_evaluation_result(
                retrieval_evaluation_result,
                retrieved_materials,
            )
            approved_materials, rejected_materials = self.service._apply_retrieval_evaluation_result(
                retrieved_materials,
                retrieval_evaluation_result,
            )
        else:
            retrieval_evaluation_result = {
                "approved_materials": [
                    {"evidence_id": str(item.get("evidence_id", "") or "")}
                    for item in retrieved_materials
                    if isinstance(item, dict)
                ],
                "rejected_materials": [],
                "rejection_breakdown": {},
                "confidence": "skipped",
                "status": "skipped",
                "skipped_reason": "feature_flag_disabled",
            }
            approved_materials = list(retrieved_materials)
            rejected_materials = []
        log_agent_flow(
            "retrieval_evaluator",
            "done",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            raw_hit_count=len(retrieved_materials),
            approved_count=len(approved_materials),
            rejected_count=len(rejected_materials),
            rejection_breakdown=retrieval_evaluation_result.get("rejection_breakdown", {}),
            confidence=str(retrieval_evaluation_result.get("confidence", "") or ""),
        )
        self.service._emit_progress(
            progress_callback,
            "retrieval_evaluator_done",
            f"章节 {section_code} 检索资料评估完成".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            approved_count=len(approved_materials),
            rejected_count=len(rejected_materials),
        )

        task_question_input = PreReviewAgentContractBuilder.task_question_input(
            planner_contract,
            raw_text=text,
            review_tasks=planner_result.get("review_tasks", []),
            retrieval_blueprint=planner_result.get("retrieval_blueprint", {}),
            retrieved_materials=approved_materials,
            rejected_materials=rejected_materials,
            retrieval_evaluation_result=retrieval_evaluation_result,
            entity_extraction_focus=planner_result.get("entity_extraction_focus", []),
            data_extraction_focus=planner_result.get("data_extraction_focus", []),
            reference_examples=reference_examples,
            historical_experience=historical_experience,
            section_rules=section_rules,
        ).to_dict()
        task_question_prompt_config = self.service._compose_runtime_prompt_config(
            project_id=project_id,
            task_type="task_question",
            base_prompt_config=base_prompt_config,
            section_id=section_id,
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            registration_class=registration_class,
        )
        self.service._emit_progress(
            progress_callback,
            "task_question_start",
            f"开始生成章节 {section_code} 的任务问题".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
        )
        task_question_result = self.service.task_question_agent.build_questions(
            task_question_input,
            prompt_config=task_question_prompt_config,
        )
        log_agent_flow(
            "task_question",
            "done",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            review_task_count=len(planner_result.get("review_tasks", [])) if isinstance(planner_result.get("review_tasks", []), list) else 0,
            task_question_count=len(task_question_result.get("task_questions", [])) if isinstance(task_question_result.get("task_questions", []), list) else 0,
            judgment_ready=bool(task_question_result.get("judgment_ready", False)),
        )
        self.service._emit_progress(
            progress_callback,
            "task_question_done",
            f"章节 {section_code} 任务问题生成完成".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            task_question_count=len(task_question_result.get("task_questions", [])) if isinstance(task_question_result.get("task_questions", []), list) else 0,
        )

        review_input = PreReviewAgentContractBuilder.reviewer_input(
            planner_contract,
            raw_text=text,
            retrieved_materials=approved_materials,
            review_tasks=planner_result.get("review_tasks", []),
            task_questions=task_question_result.get("task_questions", []),
            retrieval_blueprint=planner_result.get("retrieval_blueprint", {}),
            coverage_by_task=retrieval_evaluation_result.get("coverage_by_task", {}),
            missing_evidence_by_task=retrieval_evaluation_result.get("missing_evidence_by_task", []),
            classified_materials=retrieval_evaluation_result.get("classified_materials", {}),
            evidence_bundles_by_task=retrieval_evaluation_result.get("evidence_bundles_by_task", {}),
            entity_extraction_focus=planner_result.get("entity_extraction_focus", []),
            data_extraction_focus=planner_result.get("data_extraction_focus", []),
            reference_examples=reference_examples,
            historical_experience=historical_experience,
            section_rules=section_rules,
        ).to_dict()
        reviewer_prompt_config = self.service._compose_runtime_prompt_config(
            project_id=project_id,
            task_type="reviewer",
            base_prompt_config=base_prompt_config,
            section_id=section_id,
            section_name=section_name,
            review_domain=review_domain,
            product_type=product_type,
            registration_class=registration_class,
        )
        self.service._emit_progress(
            progress_callback,
            "reviewer_start",
            f"开始生成章节 {section_code} 的预审结论".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
        )
        review_result = self.service.planner_reviewer_agent.review(review_input, prompt_config=reviewer_prompt_config)
        review_result = self.service._cache_review_result_evidence_payload(
            review_result,
            approved_materials,
        )
        log_agent_flow(
            "reviewer",
            "done",
            run_id=run_id,
            section_id=section_id,
            section_name=section_name,
            approved_material_count=len(approved_materials),
            conclusion=str(review_result.get("pre_review_conclusion", "") or ""),
            missing_count=len(self.service._normalize_text_list(review_result.get("missing_points", []))),
            unsupported_count=len(self.service._normalize_text_list(review_result.get("unsupported_points", []))),
        )
        self.service._emit_progress(
            progress_callback,
            "reviewer_done",
            f"章节 {section_code} 预审完成".strip(),
            section_id=section_id,
            section_code=section_code,
            section_name=section_name,
            conclusion=str(review_result.get("pre_review_conclusion", "") or ""),
        )

        section_summary = {
            "structured_summary": str(review_result.get("section_summary", "") or "").strip(),
            "key_facts": self.service._normalize_text_list(review_result.get("fact_basis", {}).get("explicit_in_text", []) if isinstance(review_result.get("fact_basis", {}), dict) else []),
            "missing_items": self.service._normalize_text_list(review_result.get("missing_points", [])),
            "draft_risks": self.service._normalize_text_list(review_result.get("risk_points", [])),
            "review_tasks": planner_result.get("review_tasks", []) if isinstance(planner_result.get("review_tasks", []), list) else [],
            "task_questions": task_question_result.get("task_questions", []) if isinstance(task_question_result.get("task_questions", []), list) else [],
            "task_verdicts": review_result.get("task_verdicts", []) if isinstance(review_result.get("task_verdicts", []), list) else [],
            "reasoning_chain_items": review_result.get("reasoning_chain_items", []) if isinstance(review_result.get("reasoning_chain_items", []), list) else [],
            "problem_basis_advice_items": review_result.get("problem_basis_advice_items", []) if isinstance(review_result.get("problem_basis_advice_items", []), list) else [],
            "medical_key_entities": review_result.get("medical_key_entities", []) if isinstance(review_result.get("medical_key_entities", []), list) else [],
            "medical_key_data_points": review_result.get("medical_key_data_points", []) if isinstance(review_result.get("medical_key_data_points", []), list) else [],
            "retrieval_blueprint": planner_result.get("retrieval_blueprint", {}) if isinstance(planner_result.get("retrieval_blueprint", {}), dict) else {},
            "classified_materials": retrieval_evaluation_result.get("classified_materials", {}) if isinstance(retrieval_evaluation_result.get("classified_materials", {}), dict) else {},
            "evidence_bundles_by_task": retrieval_evaluation_result.get("evidence_bundles_by_task", {}) if isinstance(retrieval_evaluation_result.get("evidence_bundles_by_task", {}), dict) else {},
            "source_files": [str(item.get("file_name", "") or "").strip() for item in chunk.get("attached_files", []) if isinstance(item, dict)],
        }
        section_meta = {
            "section_id": section_id,
            "section_code": section_code,
            "section_name": section_name,
            "query": " | ".join(self.service._normalize_text_list(planner_result.get("query_list", []))[:3]),
            "text_preview": self.service._preview(text, max_len=220),
            "char_count": len(text),
            "page": chunk.get("page"),
            "page_start": chunk.get("page_start"),
            "page_end": chunk.get("page_end"),
            "unit_order": chunk.get("unit_order"),
            "unit_type": chunk.get("unit_type"),
            "parent_section_id": chunk.get("parent_section_id"),
            "parent_code": chunk.get("parent_code"),
            "title_path": chunk.get("title_path", []),
            "concern_points": focus_points,
            "section_rules": section_rules,
            "review_checkpoints": review_checkpoints,
            "knowledge_priority": knowledge_priority,
            "task_definition": task_definition,
            "review_tasks": planner_result.get("review_tasks", []) if isinstance(planner_result.get("review_tasks", []), list) else [],
            "retrieval_blueprint": planner_result.get("retrieval_blueprint", {}) if isinstance(planner_result.get("retrieval_blueprint", {}), dict) else {},
            "compliance_targets": compliance_targets,
            "issue_hypotheses": issue_hypotheses,
            "evidence_requirements": evidence_requirements,
            "output_requirements": output_requirements,
            "medical_entity_requirements": medical_entity_requirements,
            "medical_data_requirements": medical_data_requirements,
            "task_questions": task_question_result.get("task_questions", []) if isinstance(task_question_result.get("task_questions", []), list) else [],
            "classified_materials": retrieval_evaluation_result.get("classified_materials", {}) if isinstance(retrieval_evaluation_result.get("classified_materials", {}), dict) else {},
            "evidence_bundles_by_task": retrieval_evaluation_result.get("evidence_bundles_by_task", {}) if isinstance(retrieval_evaluation_result.get("evidence_bundles_by_task", {}), dict) else {},
            "attached_files": chunk.get("attached_files", []),
            "paragraph_blocks": paragraph_blocks,
            "section_summary": section_summary,
            "reference_examples": reference_examples,
            "previous_section_id": previous_section_meta.get("section_id", ""),
            "previous_conclusion_preview": previous_section_meta.get("conclusion_preview", ""),
        }
        coordination_payload = {
            "planner_result": planner_result,
            "retrieval": {
                "query_count": len(self.service._normalize_text_list(planner_result.get("query_list", []))),
                "focus_points": focus_points,
                "focus_point_count": len(focus_points),
                "effective_queries": self.service._merge_retrieval_queries_with_focus_points(
                    planner_result.get("query_list", []),
                    focus_points,
                ),
                "raw_hit_count": len(retrieved_materials),
                "hit_count": len(approved_materials),
                "grouped_doc_count": len({str(x.get('evidence_id', '')).split(':')[0] for x in approved_materials if str(x.get('evidence_id', '')).strip()}),
                "raw_retrieved_materials": retrieved_materials,
                "retrieved_materials": approved_materials,
                "sources": self.service._normalize_text_list([item.get("source_type", "") for item in approved_materials if isinstance(item, dict)]),
                "raw_source_breakdown": self.service._summarize_source_breakdown(retrieved_materials),
                "source_breakdown": self.service._summarize_source_breakdown(approved_materials),
            },
            "review_contract": {
                "task_definition": task_definition,
                "compliance_targets": compliance_targets,
                "issue_hypotheses": issue_hypotheses,
                "evidence_requirements": evidence_requirements,
                "output_requirements": output_requirements,
                "medical_entity_requirements": medical_entity_requirements,
                "medical_data_requirements": medical_data_requirements,
            },
            "retrieval_evaluation": {
                "historical_bad_retrieval_count": len(historical_bad_retrievals),
                "feature_flags": feature_flags,
                "approved_count": len(approved_materials),
                "rejected_count": len(rejected_materials),
                "result": retrieval_evaluation_result,
            },
            "task_question": {
                "judgment_ready": bool(task_question_result.get("judgment_ready", False)),
                "result": task_question_result,
            },
        }
        section_review_packet = self.service._build_section_review_packet(
            project_id=project_id,
            run_id=run_id,
            source_doc_id=source_doc_id,
            chunk=chunk,
            section_meta=section_meta,
            related_result={"list": approved_materials},
            coordination_payload=coordination_payload,
            run_config=run_config,
        )
        conclusion, findings, linked_rules, risk_level = self.service._map_review_result_to_legacy(review_result)
        findings = self.service._bind_findings_to_paragraphs(
            findings=findings,
            paragraph_blocks=paragraph_blocks,
            section_id=section_id,
            section_code=section_code,
        )
        score_map = {
            "supported": 0.9,
            "unsupported": 0.25,
            "insufficient_information": 0.4,
        }
        score = float(score_map.get(str(review_result.get("pre_review_conclusion", "") or ""), 0.5))

        self.service.memory_governance.remember_recent_review_conclusion(
            project_id=project_id,
            source_doc_id=source_doc_id,
            section_id=section_id,
            conclusion=conclusion,
        )

        conclusion_row = SectionConclusionRecord(
            run_id=run_id,
            section_id=section_id,
            section_name=f"{section_code} {section_name}".strip(),
            conclusion=conclusion,
            highlighted_issues=findings,
            linked_rules=linked_rules,
            risk_level=risk_level,
            create_time=self.service._now(),
        ).to_entity()
        trace_payload = self.service.projection_service.build_section_trace_payload(
            workflow_mode=workflow_mode,
            section_id=section_id,
            section_name=section_name,
            chunk=chunk,
            text=text,
            paragraph_blocks=paragraph_blocks,
            focus_points=focus_points,
            section_rules=section_rules,
            section_review_packet=section_review_packet,
            coordination_payload=coordination_payload,
            historical_experience=historical_experience,
            section_summary=section_summary,
            findings=findings,
            score=score,
            registration_class=registration_class,
            review_domain=review_domain,
            product_type=product_type,
            planner_result=planner_result,
            retrieved_materials=retrieved_materials,
            approved_materials=approved_materials,
            historical_bad_retrievals=historical_bad_retrievals,
            retrieval_evaluation_result=retrieval_evaluation_result,
            rejected_materials=rejected_materials,
            task_question_result=task_question_result,
            review_result=review_result,
            planner_prompt_config=planner_prompt_config,
            retrieval_evaluator_prompt_config=retrieval_evaluator_prompt_config,
            task_question_prompt_config=task_question_prompt_config,
            reviewer_prompt_config=reviewer_prompt_config,
        )
        audit_bundle = self.build_section_stage_audit_bundle(
            run_id=run_id,
            section_id=section_id,
            source_doc_id=source_doc_id,
            workflow_mode=workflow_mode,
            planner_input=planner_input,
            planner_result=planner_result,
            planner_prompt_config=planner_prompt_config,
            focus_points=focus_points,
            section_rules=section_rules,
            review_checkpoints=review_checkpoints,
            knowledge_priority=knowledge_priority,
            task_definition=task_definition,
            compliance_targets=compliance_targets,
            issue_hypotheses=issue_hypotheses,
            evidence_requirements=evidence_requirements,
            output_requirements=output_requirements,
            medical_entity_requirements=medical_entity_requirements,
            medical_data_requirements=medical_data_requirements,
            reference_examples=reference_examples,
            historical_experience=historical_experience,
            retrieved_materials=retrieved_materials,
            retrieval_evaluator_input=retrieval_evaluator_input,
            retrieval_evaluation_result=retrieval_evaluation_result,
            retrieval_evaluator_prompt_config=retrieval_evaluator_prompt_config,
            historical_bad_retrievals=historical_bad_retrievals,
            approved_materials=approved_materials,
            rejected_materials=rejected_materials,
            task_question_input=task_question_input,
            task_question_result=task_question_result,
            task_question_prompt_config=task_question_prompt_config,
            review_input=review_input,
            review_result=review_result,
            reviewer_prompt_config=reviewer_prompt_config,
        )
        trace_payload["io_contract"] = audit_bundle.get("io_contract", {})
        trace_row = SectionTraceRecord(
            run_id=run_id,
            section_id=section_id,
            trace_payload=trace_payload,
            create_time=self.service._now(),
        ).to_entity()
        audit_rows = audit_bundle.get("audit_rows", [])
        return {
            "success": True,
            "section_id": section_id,
            "section_meta": section_meta,
            "findings": findings,
            "score": score,
            "linked_rules": linked_rules,
            "risk_level": risk_level,
            "section_summary": section_summary,
            "planner_result": planner_result,
            "task_question_result": task_question_result,
            "review_result": review_result,
            "task_questions": task_question_result.get("task_questions", []) if isinstance(task_question_result.get("task_questions", []), list) else [],
            "task_verdicts": review_result.get("task_verdicts", []) if isinstance(review_result.get("task_verdicts", []), list) else [],
            "reasoning_chain_items": review_result.get("reasoning_chain_items", []) if isinstance(review_result.get("reasoning_chain_items", []), list) else [],
            "problem_basis_advice_items": review_result.get("problem_basis_advice_items", []) if isinstance(review_result.get("problem_basis_advice_items", []), list) else [],
            "medical_key_entities": review_result.get("medical_key_entities", []) if isinstance(review_result.get("medical_key_entities", []), list) else [],
            "medical_key_data_points": review_result.get("medical_key_data_points", []) if isinstance(review_result.get("medical_key_data_points", []), list) else [],
            "conclusion_row": conclusion_row,
            "trace_row": trace_row,
            "trace_payload": trace_payload,
            "audit_rows": audit_rows,
            "previous_section_meta": {
                "section_id": section_id,
                "conclusion_preview": self.service._preview(conclusion),
            },
        }

    def run_section_replay(
        self,
        project_id: str,
        source_doc_id: str,
        section_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.service._run_section_replay_impl(
            project_id=project_id,
            source_doc_id=source_doc_id,
            section_id=section_id,
            run_config=run_config,
            progress_callback=progress_callback,
        )

    def replay_section_review(
        self,
        project_id: str,
        source_doc_id: str,
        section_id: str,
        run_config: Optional[Dict[str, Any]] = None,
        progress_callback=None,
    ) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        return self.run_section_replay(
            project_id=project_id,
            source_doc_id=source_doc_id,
            section_id=section_id,
            run_config=run_config,
            progress_callback=progress_callback,
        )

    def build_section_stage_audit_bundle(
        self,
        *,
        run_id: str,
        section_id: str,
        source_doc_id: str,
        workflow_mode: str,
        planner_input: Dict[str, Any],
        planner_result: Dict[str, Any],
        planner_prompt_config: Optional[Dict[str, Any]],
        focus_points: List[str],
        section_rules: List[str],
        review_checkpoints: List[str],
        knowledge_priority: List[str],
        task_definition: Dict[str, Any],
        medical_entity_requirements: List[str],
        medical_data_requirements: List[str],
        reference_examples: List[Dict[str, Any]],
        historical_experience: List[Dict[str, Any]],
        retrieved_materials: List[Dict[str, Any]],
        retrieval_evaluator_input: Dict[str, Any],
        retrieval_evaluation_result: Dict[str, Any],
        retrieval_evaluator_prompt_config: Optional[Dict[str, Any]],
        historical_bad_retrievals: List[Dict[str, Any]],
        approved_materials: List[Dict[str, Any]],
        rejected_materials: List[Dict[str, Any]],
        task_question_input: Dict[str, Any],
        task_question_result: Dict[str, Any],
        task_question_prompt_config: Optional[Dict[str, Any]],
        review_input: Dict[str, Any],
        review_result: Dict[str, Any],
        reviewer_prompt_config: Optional[Dict[str, Any]],
        compliance_targets: Optional[List[str]] = None,
        issue_hypotheses: Optional[List[str]] = None,
        evidence_requirements: Optional[List[str]] = None,
        output_requirements: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        task_definition = task_definition or {}
        compliance_targets = compliance_targets or []
        issue_hypotheses = issue_hypotheses or []
        evidence_requirements = evidence_requirements or []
        output_requirements = output_requirements or []
        medical_entity_requirements = medical_entity_requirements or []
        medical_data_requirements = medical_data_requirements or []
        stage_digests = self._build_section_stage_digests(
            planner_input=planner_input,
            planner_result=planner_result,
            focus_points=focus_points,
            section_rules=section_rules,
            review_checkpoints=review_checkpoints,
            knowledge_priority=knowledge_priority,
            task_definition=task_definition,
            compliance_targets=compliance_targets,
            issue_hypotheses=issue_hypotheses,
            evidence_requirements=evidence_requirements,
            output_requirements=output_requirements,
            medical_entity_requirements=medical_entity_requirements,
            medical_data_requirements=medical_data_requirements,
            reference_examples=reference_examples,
            historical_experience=historical_experience,
            retrieved_materials=retrieved_materials,
            retrieval_evaluator_input=retrieval_evaluator_input,
            retrieval_evaluation_result=retrieval_evaluation_result,
            historical_bad_retrievals=historical_bad_retrievals,
            approved_materials=approved_materials,
            rejected_materials=rejected_materials,
            task_question_input=task_question_input,
            task_question_result=task_question_result,
            review_input=review_input,
            review_result=review_result,
        )
        audit_rows = [
            (
                getattr(self.service, "execution_audit_service", None).build_execution_audit_row
                if getattr(self.service, "execution_audit_service", None) is not None
                else self.service._build_execution_audit_row
            )(
                run_id=run_id,
                section_id=section_id,
                stage=stage,
                agent_name=payload["agent_name"],
                input_digest=payload["input"],
                output_digest=payload["output"],
                prompt_config=payload["prompt_config"],
                extra_version={
                    "source_doc_id": source_doc_id,
                    "workflow_mode": workflow_mode,
                },
            )
            for stage, payload in [
                ("planner", {**stage_digests["planner"], "agent_name": "planner", "prompt_config": planner_prompt_config}),
                ("retrieval", {**stage_digests["retrieval"], "agent_name": "retrieval", "prompt_config": planner_prompt_config}),
                (
                    "retrieval_evaluator",
                    {
                        **stage_digests["retrieval_evaluator"],
                        "agent_name": "retrieval_evaluator",
                        "prompt_config": retrieval_evaluator_prompt_config,
                    },
                ),
                (
                    "task_question",
                    {
                        **stage_digests["task_question"],
                        "agent_name": "task_question",
                        "prompt_config": task_question_prompt_config,
                    },
                ),
                ("reviewer", {**stage_digests["reviewer"], "agent_name": "reviewer", "prompt_config": reviewer_prompt_config}),
            ]
        ]
        io_contract = {
            stage: {
                "input": payload["input"],
                "output": payload["output"],
            }
            for stage, payload in stage_digests.items()
        }
        return {
            "io_contract": io_contract,
            "audit_rows": audit_rows,
        }

    def _build_section_stage_digests(
        self,
        *,
        planner_input: Dict[str, Any],
        planner_result: Dict[str, Any],
        focus_points: List[str],
        section_rules: List[str],
        review_checkpoints: List[str],
        knowledge_priority: List[str],
        task_definition: Dict[str, Any],
        medical_entity_requirements: List[str],
        medical_data_requirements: List[str],
        reference_examples: List[Dict[str, Any]],
        historical_experience: List[Dict[str, Any]],
        retrieved_materials: List[Dict[str, Any]],
        retrieval_evaluator_input: Dict[str, Any],
        retrieval_evaluation_result: Dict[str, Any],
        historical_bad_retrievals: List[Dict[str, Any]],
        approved_materials: List[Dict[str, Any]],
        rejected_materials: List[Dict[str, Any]],
        task_question_input: Dict[str, Any],
        task_question_result: Dict[str, Any],
        review_input: Dict[str, Any],
        review_result: Dict[str, Any],
        compliance_targets: Optional[List[str]] = None,
        issue_hypotheses: Optional[List[str]] = None,
        evidence_requirements: Optional[List[str]] = None,
        output_requirements: Optional[List[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        task_definition = task_definition or {}
        compliance_targets = compliance_targets or []
        issue_hypotheses = issue_hypotheses or []
        evidence_requirements = evidence_requirements or []
        output_requirements = output_requirements or []
        medical_entity_requirements = medical_entity_requirements or []
        medical_data_requirements = medical_data_requirements or []
        normalize = self.service._normalize_text_list
        return {
            "planner": {
                "input": {
                    "section_id": planner_input.get("section_id", ""),
                    "section_name": planner_input.get("section_name", ""),
                    "registration_class": planner_input.get("registration_class", ""),
                    "review_domain": planner_input.get("review_domain", ""),
                    "product_type": planner_input.get("product_type", ""),
                    "focus_points": normalize(planner_input.get("focus_points", [])),
                    "review_checkpoints": normalize(review_checkpoints),
                    "knowledge_priority": normalize(knowledge_priority),
                    "task_definition": dict(task_definition),
                    "compliance_targets": normalize(compliance_targets),
                    "issue_hypotheses": normalize(issue_hypotheses),
                    "evidence_requirements": normalize(evidence_requirements),
                    "output_requirements": normalize(output_requirements),
                    "medical_entity_requirements": normalize(medical_entity_requirements),
                    "medical_data_requirements": normalize(medical_data_requirements),
                    "section_rule_count": len(section_rules),
                    "reference_example_count": len(reference_examples),
                    "historical_experience_count": len(historical_experience),
                },
                "output": {
                    "query_list": normalize(planner_result.get("query_list", [])),
                    "retrieval_plan": planner_result.get("retrieval_plan", []),
                    "priority_sources": normalize(planner_result.get("priority_sources", [])),
                    "entity_extraction_focus": normalize(planner_result.get("entity_extraction_focus", [])),
                    "data_extraction_focus": normalize(planner_result.get("data_extraction_focus", [])),
                    "missing_info_flags": normalize(planner_result.get("missing_info_flags", [])),
                },
            },
            "retrieval": {
                "input": {
                    "query_list": normalize(planner_result.get("query_list", [])),
                    "source_types": normalize(
                        [item.get("source_type", "") for item in planner_result.get("retrieval_plan", []) if isinstance(item, dict)]
                    ),
                    "focus_points": normalize(focus_points),
                    "review_checkpoints": normalize(review_checkpoints),
                    "compliance_targets": normalize(compliance_targets),
                    "issue_hypotheses": normalize(issue_hypotheses),
                    "medical_entity_requirements": normalize(medical_entity_requirements),
                    "medical_data_requirements": normalize(medical_data_requirements),
                    "historical_experience_count": len(historical_experience),
                },
                "output": {
                    "raw_hit_count": len(retrieved_materials),
                    "source_breakdown": self.service._summarize_source_breakdown(retrieved_materials),
                    "top_evidence_ids": normalize(
                        [str(item.get("evidence_id", "") or "") for item in retrieved_materials if isinstance(item, dict)]
                    )[:8],
                },
            },
            "retrieval_evaluator": {
                "input": {
                    "section_id": retrieval_evaluator_input.get("section_id", ""),
                    "focus_points": normalize(retrieval_evaluator_input.get("focus_points", [])),
                    "review_checkpoints": normalize(retrieval_evaluator_input.get("review_checkpoints", [])),
                    "compliance_targets": normalize(retrieval_evaluator_input.get("compliance_targets", [])),
                    "evidence_requirements": normalize(retrieval_evaluator_input.get("evidence_requirements", [])),
                    "medical_entity_requirements": normalize(retrieval_evaluator_input.get("medical_entity_requirements", [])),
                    "medical_data_requirements": normalize(retrieval_evaluator_input.get("medical_data_requirements", [])),
                    "section_rule_count": len(section_rules),
                    "retrieved_material_count": len(retrieved_materials),
                    "historical_bad_retrieval_count": len(historical_bad_retrievals),
                    "top_candidate_ids": normalize(
                        [str(item.get("evidence_id", "") or "") for item in retrieved_materials if isinstance(item, dict)]
                    )[:8],
                },
                "output": {
                    "approved_count": len(approved_materials),
                    "rejected_count": len(rejected_materials),
                    "approved_evidence_ids": normalize(
                        [str(item.get("evidence_id", "") or "") for item in approved_materials if isinstance(item, dict)]
                    )[:8],
                    "rejected_evidence_ids": normalize(
                        [str(item.get("evidence_id", "") or "") for item in rejected_materials if isinstance(item, dict)]
                    )[:8],
                    "rejection_breakdown": retrieval_evaluation_result.get("rejection_breakdown", {}),
                    "confidence": str(retrieval_evaluation_result.get("confidence", "") or ""),
                },
            },
            "task_question": {
                "input": {
                    "section_id": task_question_input.get("section_id", ""),
                    "review_task_count": len(
                        [
                            item
                            for item in task_question_input.get("review_tasks", [])
                            if isinstance(task_question_input.get("review_tasks", []), list) and isinstance(item, dict)
                        ]
                    ),
                    "entity_extraction_focus": normalize(task_question_input.get("entity_extraction_focus", [])),
                    "data_extraction_focus": normalize(task_question_input.get("data_extraction_focus", [])),
                    "approved_material_count": len(approved_materials),
                    "rejected_material_count": len(rejected_materials),
                    "coverage_task_count": len(
                        task_question_input.get("retrieval_evaluation_result", {}).get("coverage_by_task", {})
                    )
                    if isinstance(task_question_input.get("retrieval_evaluation_result", {}), dict)
                    and isinstance(task_question_input.get("retrieval_evaluation_result", {}).get("coverage_by_task", {}), dict)
                    else 0,
                },
                "output": {
                    "judgment_ready": bool(task_question_result.get("judgment_ready", False)),
                    "task_question_count": len(normalize([item.get("task_question", "") for item in task_question_result.get("task_questions", []) if isinstance(item, dict)])),
                    "missing_information": normalize(task_question_result.get("missing_information", [])),
                },
            },
            "reviewer": {
                "input": {
                    "section_id": review_input.get("section_id", ""),
                    "section_name": review_input.get("section_name", ""),
                    "focus_points": normalize(review_input.get("focus_points", [])),
                    "review_checkpoints": normalize(review_input.get("review_checkpoints", [])),
                    "compliance_targets": normalize(review_input.get("compliance_targets", [])),
                    "output_requirements": normalize(review_input.get("output_requirements", [])),
                    "review_task_count": len(
                        [
                            item
                            for item in review_input.get("review_tasks", [])
                            if isinstance(review_input.get("review_tasks", []), list) and isinstance(item, dict)
                        ]
                    ),
                    "task_question_count": len(
                        [
                            item
                            for item in review_input.get("task_questions", [])
                            if isinstance(review_input.get("task_questions", []), list) and isinstance(item, dict)
                        ]
                    ),
                    "medical_entity_requirements": normalize(review_input.get("medical_entity_requirements", [])),
                    "medical_data_requirements": normalize(review_input.get("medical_data_requirements", [])),
                    "section_rule_count": len(section_rules),
                    "approved_material_count": len(approved_materials),
                    "approved_evidence_ids": normalize(
                        [str(item.get("evidence_id", "") or "") for item in approved_materials if isinstance(item, dict)]
                    )[:8],
                },
                "output": {
                    "pre_review_conclusion": str(review_result.get("pre_review_conclusion", "") or ""),
                    "confidence": str(review_result.get("confidence", "") or ""),
                    "supported_count": len(normalize(review_result.get("supported_points", []))),
                    "unsupported_count": len(normalize(review_result.get("unsupported_points", []))),
                    "missing_count": len(normalize(review_result.get("missing_points", []))),
                    "risk_count": len(normalize(review_result.get("risk_points", []))),
                    "linked_rule_count": len(normalize(review_result.get("linked_rules", []))),
                    "task_verdict_count": len(review_result.get("task_verdicts", [])) if isinstance(review_result.get("task_verdicts", []), list) else 0,
                    "reasoning_chain_count": len(review_result.get("reasoning_chain_items", [])) if isinstance(review_result.get("reasoning_chain_items", []), list) else 0,
                    "medical_entity_count": len(review_result.get("medical_key_entities", [])) if isinstance(review_result.get("medical_key_entities", []), list) else 0,
                    "medical_data_count": len(review_result.get("medical_key_data_points", [])) if isinstance(review_result.get("medical_key_data_points", []), list) else 0,
                },
            },
        }
