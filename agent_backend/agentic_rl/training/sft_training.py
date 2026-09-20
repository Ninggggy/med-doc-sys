def run_sft(dataset, model_name: str = "base-model"):
    return {
        "model": model_name,
        "samples": len(dataset),
        "status": "sft_completed",
    }
