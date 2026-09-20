from __future__ import annotations

from typing import Any, Dict


class ReportGenerator:
    """Generate evaluation reports for replay and feedback metrics."""

    def generate_evaluation_report(self, metrics: Dict[str, Any], compare_result: Dict[str, Any]) -> Dict[str, Any]:
        """Build structured evaluation-report payload."""
        return {"metrics": metrics, "compare_result": compare_result}

    def render_markdown_report(self, report_data: Dict[str, Any]) -> str:
        """Render one markdown report string."""
        return f"# Evaluation Report\n\n```json\n{report_data}\n```"
