class RLAdapter:
    def format_sample(self, prompt: str, response: str, reward: float):
        return {"prompt": prompt, "response": response, "reward": reward}
