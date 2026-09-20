def human_score(final_report: str, score: int, reviewer: str):
    return {
        "reviewer": reviewer,
        "score": max(0, min(100, score)),
        "report_length": len(final_report),
    }
