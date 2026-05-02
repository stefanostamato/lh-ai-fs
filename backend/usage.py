from dataclasses import dataclass


@dataclass
class UsageCollector:
    prompt: int = 0
    completion: int = 0

    def add(self, prompt: int, completion: int) -> None:
        self.prompt += prompt
        self.completion += completion
