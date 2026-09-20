def run_grpo(dataset, model_name: str = "base-model"):
    return {
        "model": model_name,
        "samples": len(dataset),
        "status": "grpo_completed",
    }
