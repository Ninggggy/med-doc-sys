import re


PAGE_MARKER_RE = re.compile(r"^>\s*page\s*:\s*\d+\s*$", re.IGNORECASE)
PAGE_DECORATION_RE = re.compile(r"^[\-\u2013\u2014\s]*\d+[\-\u2013\u2014\s]*$")
FENCE_OPEN_RE = re.compile(r"^```(?:formula|image|table-frame)\s*$", re.IGNORECASE)
FENCE_CLOSE_RE = re.compile(r"^```\s*$")


def sanitize_parser_text(text: str) -> str:
    value = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not value.strip():
        return ""

    lines = []
    for raw_line in value.split("\n"):
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            if lines and lines[-1] != "":
                lines.append("")
            continue
        if PAGE_MARKER_RE.match(stripped):
            continue
        if PAGE_DECORATION_RE.match(stripped):
            continue
        if FENCE_OPEN_RE.match(stripped):
            continue
        if FENCE_CLOSE_RE.match(stripped):
            continue
        lines.append(stripped)

    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()
