from typing import List


class PreReviewNodes:
    """
    Compatibility helper for legacy imports.
    Primary production logic lives in `agents.multi_agent.workflow`.
    """

    @staticmethod
    def extract_basic_findings(text: str) -> List[str]:
        low = (text or "").lower()
        findings: List[str] = []
        if "contraindication" in low or "禁忌" in (text or ""):
            findings.append("需核对禁忌信息完整性。")
        if "adverse reaction" in low or "不良反应" in (text or ""):
            findings.append("需核对不良反应分级与处置说明。")
        if "dosage" in low or "用法用量" in (text or "") or "剂量" in (text or ""):
            findings.append("需核查剂量和特殊人群给药规则。")
        return findings

