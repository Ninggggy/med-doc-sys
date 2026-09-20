def llm_score(final_report: str, rubric: dict | None = None):
    rubric = rubric or {"evidence": 0.4, "logic": 0.3, "coverage": 0.3}
    base = min(len(final_report) / 2000.0, 1.0)
    return {
        "score": round(base * 100, 2),
        "rubric": rubric,
    }
