from agent.agent_backend.agents.pre_review_agent.pre_review_agent import build_pre_review_agent


def compile_agents():
    """
    Register orchestrator and role-level capabilities.
    """
    pre_review_agent = build_pre_review_agent()
    role_meta = pre_review_agent.describe_roles()
    role_map = {x["name"]: x for x in role_meta}
    return {
        "pre_review_agent": pre_review_agent,
        "planner_agent": role_map.get("planner", {"role": "planner"}),
        "retriever_agent": role_map.get("retriever", {"role": "retriever"}),
        "reviewer_agent": role_map.get("reviewer", {"role": "reviewer"}),
        "reflector_agent": role_map.get("reflector", {"role": "reflector"}),
        "synthesizer_agent": role_map.get("synthesizer", {"role": "synthesizer"}),
    }
