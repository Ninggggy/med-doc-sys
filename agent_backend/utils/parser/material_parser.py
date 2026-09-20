from typing import List, Dict


def parse_material(file_path: str) -> List[Dict]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        text = f.read()
    lines = [x.strip() for x in text.splitlines() if x.strip()]
    chunks = [{"chunk_id": f"m_{i+1}", "page": None, "text": line} for i, line in enumerate(lines)]
    return chunks
