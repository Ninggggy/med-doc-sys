from dataclasses import dataclass
from typing import Any, Dict, List


@dataclass
class GSSCContext:
    gathered: List[Dict[str, Any]]
    selected: List[Dict[str, Any]]
    structured: Dict[str, Any]
    compressed: str


class ContextBuilder:
    """
    GSSC:
    1) Gather: normalize candidate sources
    2) Select: rank + deduplicate + budget
    3) Structure: build grouped context object
    4) Compress: flatten to prompt-safe text
    """

    def gather(self, sources: List[Any]) -> List[Dict[str, Any]]:
        gathered: List[Dict[str, Any]] = []
        for i, s in enumerate(sources):
            if isinstance(s, dict):
                text = str(s.get("text") or s.get("value") or "").strip()
                score = float(s.get("score", 0.0))
                source = str(s.get("source", s.get("memory_type", "unknown")))
                meta = s.get("metadata", {})
            else:
                text = str(s).strip()
                score = 0.0
                source = "unknown"
                meta = {}
            if not text:
                continue
            gathered.append(
                {
                    "id": i,
                    "text": text,
                    "score": score,
                    "source": source,
                    "metadata": meta,
                    "length": len(text),
                }
            )
        return gathered

    def select(
        self,
        gathered: List[Dict[str, Any]],
        max_items: int = 10,
        max_chars: int = 2400,
    ) -> List[Dict[str, Any]]:
        ranked = sorted(gathered, key=lambda x: (x["score"], -x["length"]), reverse=True)
        selected: List[Dict[str, Any]] = []
        used_chars = 0
        seen_text = set()
        for r in ranked:
            sig = r["text"][:120]
            if sig in seen_text:
                continue
            if len(selected) >= max_items:
                break
            if used_chars + r["length"] > max_chars:
                continue
            selected.append(r)
            seen_text.add(sig)
            used_chars += r["length"]
        return selected

    def structure(self, selected: List[Dict[str, Any]]) -> Dict[str, Any]:
        by_source: Dict[str, List[Dict[str, Any]]] = {}
        for item in selected:
            by_source.setdefault(item["source"], []).append(item)
        return {
            "total": len(selected),
            "by_source": by_source,
            "items": selected,
        }

    def compress(self, structured: Dict[str, Any], max_chars: int = 1500) -> str:
        lines = []
        for src, items in structured.get("by_source", {}).items():
            lines.append(f"## {src}")
            for i, it in enumerate(items, start=1):
                lines.append(f"{i}. {it['text']}")
        merged = "\n".join(lines)
        return merged[:max_chars]

    def build(
        self,
        sources: List[Any],
        max_items: int = 10,
        source_char_budget: int = 2400,
        output_char_budget: int = 1500,
    ) -> GSSCContext:
        g = self.gather(sources)
        s = self.select(g, max_items=max_items, max_chars=source_char_budget)
        st = self.structure(s)
        c = self.compress(st, max_chars=output_char_budget)
        return GSSCContext(g, s, st, c)

