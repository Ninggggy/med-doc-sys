from __future__ import annotations

from typing import Dict, Iterable


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def length_reward(text: str) -> float:
    return clamp01(len(text or "") / 1000.0)


def keyword_reward(text: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    source = text or ""
    hit = sum(1 for k in keywords if k and k in source)
    return clamp01(hit / len(keywords))


def feedback_metrics(feedback_types: Iterable[str]) -> Dict[str, float]:
    """
    Treat feedback as a confusion-like signal:
    - valid -> TP
    - false_positive -> FP
    - missed -> FN
    """
    tp = fp = fn = 0
    for t in feedback_types:
        if t == "valid":
            tp += 1
        elif t == "false_positive":
            fp += 1
        elif t == "missed":
            fn += 1

    total = tp + fp + fn
    accuracy = safe_ratio(tp, total)
    precision = safe_ratio(tp, tp + fp)
    recall = safe_ratio(tp, tp + fn)
    f1 = safe_ratio(2 * precision * recall, precision + recall)

    fp_rate = safe_ratio(fp, total)
    fn_rate = safe_ratio(fn, total)

    # Reward emphasizes balanced quality, with stronger penalty for missed issues.
    reward_score = (
        0.35 * f1
        + 0.25 * accuracy
        + 0.20 * precision
        + 0.20 * recall
        - 0.10 * fp_rate
        - 0.20 * fn_rate
    )

    return {
        "feedback_total": float(total),
        "tp_valid": float(tp),
        "fp_false_positive": float(fp),
        "fn_missed": float(fn),
        "accuracy": clamp01(accuracy),
        "precision": clamp01(precision),
        "recall": clamp01(recall),
        "f1": clamp01(f1),
        "reward_score": clamp01(reward_score),
    }
