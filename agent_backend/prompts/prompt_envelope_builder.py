from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List


@dataclass(frozen=True)
class PromptEnvelopeSpec:
    role_name: str
    mission: str
    protocol_version: str
    hard_rules: List[str]
    protocol_steps: List[str]
    validation_checklist: List[str]


class PromptEnvelopeBuilder:
    """Build layered prompt messages for core review agents."""

    ENVELOPE_VERSION = "prompt_envelope_v1"

    _COMMON_RULES = [
        "你只能输出目标 schema 对应的 JSON，不得输出 markdown、解释、前后缀说明或多余文本。",
        "你可以在内部进行逐步分析，但不得暴露思维链、草稿、自检过程或中间推理。",
        "不得编造缺失事实；材料不足时，只能按 schema 明确标注信息不足、缺失项或不确定项。",
        "必须优先服从上游结构化约束、当前任务边界和输出 schema，不得擅自扩展字段。",
    ]

    _SPECS: Dict[str, PromptEnvelopeSpec] = {
        "planner": PromptEnvelopeSpec(
            role_name="章节规划智能体",
            mission="围绕当前章节生成高质量检索计划，不能输出审评结论。",
            protocol_version="planner_protocol_v1",
            hard_rules=[
                "query 必须围绕章节主题、关注点、产品类型和注册分类组织，避免空泛或只输出章节编号。",
                "retrieval_plan 必须说明来源、目的和 query 子集。",
            ],
            protocol_steps=[
                "先识别章节对象、产品类型、关注点和缺失信息。",
                "再将检索目标拆为多来源计划，控制 query 数量和粒度。",
                "最后按 schema 输出 query_list、retrieval_plan 和缺失信息标记。",
            ],
            validation_checklist=[
                "是否没有输出任何审评结论。",
                "query 是否短、可检索、与章节直接相关。",
                "retrieval_plan 是否覆盖主要来源并与 query_list 对齐。",
            ],
        ),
        "reviewer": PromptEnvelopeSpec(
            role_name="章节审评智能体",
            mission="基于章节原文、通过检索评估的证据、章节规则和关注点生成结构化审评结果。",
            protocol_version="reviewer_protocol_v1",
            hard_rules=[
                "所有结论都必须受章节原文、规则和通过证据约束。",
                "不得输出 partially_supported 等模糊结论；只能输出 schema 允许的明确结论值。",
                "历史经验只能作为风险提醒，不能替代法规或原文证据。",
            ],
            protocol_steps=[
                "先核对章节原文是否直接支持关注点和规则要求。",
                "再核对通过的检索证据能否补充支持或指出缺失。",
                "最后逐条形成支持点、缺失点、风险点、问题清单和结论。",
            ],
            validation_checklist=[
                "每个关注点是否都被覆盖。",
                "事实依据和规则依据是否可追溯。",
                "结论是否明确且与支持/缺失/问题清单一致。",
            ],
        ),
        "retrieval_evaluator": PromptEnvelopeSpec(
            role_name="检索评估智能体",
            mission="评估候选资料是否真正服务于当前章节，不输出最终合规结论。",
            protocol_version="retrieval_evaluator_protocol_v1",
            hard_rules=[
                "只有确实有助于判断当前章节、关注点或章节规则的资料才能通过。",
                "如果资料针对的是其他药物、其他对象或其他场景，必须拒绝。",
                "通用资料只有在能直接支撑章节意图或规则解释时才能通过。",
            ],
            protocol_steps=[
                "先识别当前章节的核心对象、主题和关注点。",
                "再逐条评估候选资料的对象一致性、意图相关性和历史负反馈风险。",
                "最后按 schema 输出通过资料、拒绝资料和拒绝原因统计。",
            ],
            validation_checklist=[
                "是否避免把错药物、错对象或错章节证据放行。",
                "是否没有输出最终审评结论。",
                "每条拒绝资料是否给出了可审计的 reject_type 和 reject_reason。",
            ],
        ),
        "task_question": PromptEnvelopeSpec(
            role_name="任务问题提出智能体",
            mission="把规则约束、章节事实和已通过证据转成显式待判断问题，不直接输出最终结论。",
            protocol_version="task_question_protocol_v1",
            hard_rules=[
                "每个 task_question 都必须围绕明确的 review_task，不能退化成泛化摘要。",
                "如果证据不足，必须明确标出 missing_information，而不是提前替 reviewer 下结论。",
                "只能输出 schema 允许的任务问题、判断准备度和缺失信息。",
            ],
            protocol_steps=[
                "先读取 review_tasks，确认每个任务的对象、规则和比较维度。",
                "再结合通过证据和检索覆盖情况，判断每个任务是否已具备判断条件。",
                "最后输出待判断问题、支撑证据、缺失信息和 reviewer 后续推理计划。",
            ],
            validation_checklist=[
                "是否没有提前输出 supported 或 unsupported 等最终结论。",
                "每个 task_question 是否都能回溯到 review_task 和证据覆盖情况。",
                "missing_information 是否真正对应当前任务缺口。",
            ],
        ),
        "feedback_analyzer": PromptEnvelopeSpec(
            role_name="反馈归因智能体",
            mission="根据反馈信号、trace 和章节结果做结构化归因，不直接修改模板。",
            protocol_version="feedback_analyzer_protocol_v1",
            hard_rules=[
                "error_types 必须优先落到固定 taxonomy 中。",
                "优先使用 trace、证据反馈和检索轨迹做归因，不能只复述用户意见。",
            ],
            protocol_steps=[
                "先识别反馈极性和主要错误位置。",
                "再结合 trace、证据和规则判断根因。",
                "最后输出错误类型、根因和可复用的新经验片段。",
            ],
            validation_checklist=[
                "归因是否与用户反馈和 trace 一致。",
                "new_experience 是否可复用为规则句，而不是单次吐槽。",
                "是否没有越权生成 patch。",
            ],
        ),
        "feedback_optimizer": PromptEnvelopeSpec(
            role_name="反馈优化智能体",
            mission="根据归因结果生成最小 patch，只优化必要 agent。",
            protocol_version="feedback_optimizer_protocol_v1",
            hard_rules=[
                "只能生成最小 patch，不得重写整份模板。",
                "knowledge understanding 问题优先改 planner；query 问题只改 planner 或 retrieval_evaluator；推理链问题优先改 task_question 或 reviewer；表达问题只改 reviewer。",
                "candidate_templates 只能是最小增量修补后的候选版本。",
            ],
            protocol_steps=[
                "先识别主错误类型和目标 agent。",
                "再生成最小 patch 及触发条件。",
                "最后输出 patch 列表和候选模板快照。",
            ],
            validation_checklist=[
                "patch 是否最小且可执行。",
                "target_agent 是否与 patch_type 一致。",
                "是否避免产生无关模板改写。",
            ],
        ),
        "p52_feedback_optimizer": PromptEnvelopeSpec(
            role_name="P52 反馈优化智能体",
            mission="根据 3.2.P.5.2 反馈与 trace 生成可执行的运行时 patch proposal，不输出审评结论。",
            protocol_version="p52_feedback_optimizer_protocol_v1",
            hard_rules=[
                "patch 必须是最小运行时 overlay，而不是泛泛建议。",
                "优先修上游结构化判断、规则适用边界和检索范围，再修 reviewer 表达。",
                "approved patch 将进入运行时 overlay，因此 patch 内容必须可直接被程序消费。",
            ],
            protocol_steps=[
                "先读取反馈、trace、方法域和规则候选，定位真正出错的链路层。",
                "再生成最小 patch proposal，并给出 overlay 字段和验证关注点。",
                "最后按 schema 输出 patch 列表，不要输出无关解释。",
            ],
            validation_checklist=[
                "patch 是否具体到可执行 overlay 字段。",
                "target_agent 与 patch_type 是否一致。",
                "verification_focus 是否能直接用于回放验证。",
            ],
        ),
        "meta_reflector": PromptEnvelopeSpec(
            role_name="元反思智能体",
            mission="将一次完整章节闭环蒸馏为可复用经验、few-shot 示例和 bad case 资产。",
            protocol_version="meta_reflector_protocol_v1",
            hard_rules=[
                "经验必须来源于真实输入、检索、结论和反馈，不得虚构理想答案。",
                "few-shot 示例必须可复用，不能只是重复用户反馈原文。",
            ],
            protocol_steps=[
                "先回顾章节输入、证据、结论和反馈。",
                "再抽取 bad case、高频资料和稳定经验。",
                "最后输出经验条目和 few-shot 示例。",
            ],
            validation_checklist=[
                "经验是否具有复用价值而非一次性描述。",
                "few_shot_example 是否包含清晰的输入快照与期望输出。",
                "high_frequency_doc_ids 是否与本轮检索上下文一致。",
            ],
        ),
    }

    @classmethod
    def build(cls, task_type: str, user_material: str) -> List[Dict[str, str]]:
        spec = cls._SPECS.get(task_type)
        if not spec:
            raise ValueError(f"unsupported prompt envelope task_type: {task_type}")
        return [
            {"role": "system", "content": cls._build_system(spec)},
            {"role": "assistant", "content": cls._build_assistant(spec)},
            {"role": "user", "content": cls._build_user(user_material)},
        ]

    @classmethod
    def version_info(cls, task_type: str) -> Dict[str, str]:
        spec = cls._SPECS.get(task_type)
        if not spec:
            raise ValueError(f"unsupported prompt envelope task_type: {task_type}")
        return {
            "task_type": str(task_type or "").strip(),
            "envelope_version": cls.ENVELOPE_VERSION,
            "protocol_version": spec.protocol_version,
        }

    @classmethod
    def _build_system(cls, spec: PromptEnvelopeSpec) -> str:
        lines = [
            f"你是药品预审系统中的“{spec.role_name}”。",
            f"你的任务目标：{spec.mission}",
            "",
            "宪法约束：",
        ]
        lines.extend(cls._numbered(cls._COMMON_RULES + spec.hard_rules))
        return "\n".join(lines).strip()

    @staticmethod
    def _build_assistant(spec: PromptEnvelopeSpec) -> str:
        lines = [
            "作业协议（仅在内部执行，不得输出以下步骤或检查清单）：",
        ]
        lines.extend(PromptEnvelopeBuilder._numbered(spec.protocol_steps))
        lines.append("")
        lines.append("输出前校验清单（仅内部执行，不得输出）：")
        lines.extend(PromptEnvelopeBuilder._numbered(spec.validation_checklist))
        return "\n".join(lines).strip()

    @staticmethod
    def _build_user(user_material: str) -> str:
        content = str(user_material or "").strip()
        if not content:
            content = "当前没有业务材料。若材料不足，请严格按 schema 返回信息不足。"
        return f"以下是当前任务的业务材料。请仅基于这些材料完成任务，并只输出 JSON。\n\n{content}".strip()

    @staticmethod
    def _numbered(items: List[str]) -> List[str]:
        return [f"{index}. {str(item).strip()}" for index, item in enumerate(items, start=1) if str(item).strip()]
