from dataclasses import dataclass, field
from typing import List


@dataclass
class AgentRole:
    name: str
    description: str
    capabilities: List[str] = field(default_factory=list)
    collaboration_contract: str = ""
    inputs: List[str] = field(default_factory=list)
    outputs: List[str] = field(default_factory=list)


def build_default_roles() -> List[AgentRole]:
    return [
        AgentRole(
            name="planner",
            description="Decompose section review task into executable plan steps.",
            capabilities=[
                "Identify review objective and scope",
                "Select required evidence dimensions",
                "Generate deterministic plan steps",
            ],
            collaboration_contract="Receives section text, outputs plan_steps for retriever/reviewer.",
            inputs=["doc_id", "section_id", "content"],
            outputs=["plan_steps"],
        ),
        AgentRole(
            name="retriever",
            description="Normalize and rank rule evidence for the current section.",
            capabilities=[
                "Filter invalid evidence",
                "Construct concise evidence snippets",
                "Rank and deduplicate rule references",
            ],
            collaboration_contract="Consumes plan_steps and related_rules, outputs rule_evidence.",
            inputs=["plan_steps", "related_rules"],
            outputs=["rule_evidence"],
        ),
        AgentRole(
            name="reviewer",
            description="Generate issue findings from content, memory and rule evidence.",
            capabilities=[
                "Risk keyword detection",
                "Cross-check memory and evidence",
                "Produce draft findings",
            ],
            collaboration_contract="Consumes content/memory/evidence, outputs findings_draft.",
            inputs=["content", "memory_context", "rule_evidence"],
            outputs=["findings_draft"],
        ),
        AgentRole(
            name="reflector",
            description="Audit draft findings for false positives/omissions.",
            capabilities=[
                "Conservative fallback when evidence exists",
                "Deduplicate and trim outputs",
                "Mark confidence notes",
            ],
            collaboration_contract="Consumes findings_draft + evidence, outputs findings_refined.",
            inputs=["findings_draft", "rule_evidence", "plan_steps"],
            outputs=["findings_refined"],
        ),
        AgentRole(
            name="synthesizer",
            description="Convert refined findings into final conclusion and risk score.",
            capabilities=[
                "Risk scoring",
                "Conclusion synthesis",
                "Trace summary generation",
            ],
            collaboration_contract="Consumes findings_refined, outputs findings/score/conclusion.",
            inputs=["findings_refined"],
            outputs=["findings", "score", "conclusion"],
        ),
    ]

