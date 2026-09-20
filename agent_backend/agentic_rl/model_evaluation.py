def evaluate_model(predictions: list[str], references: list[str]):
    total = min(len(predictions), len(references))
    if total == 0:
        return {"score": 0.0}
    hit = sum(1 for p, r in zip(predictions, references) if p.strip() == r.strip())
    return {"score": hit / total, "total": total}
