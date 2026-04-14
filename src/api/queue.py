from dataclasses import dataclass
from typing import Any


@dataclass
class QueueMessage:
    topic: str
    payload: dict[str, Any]


class InMemoryQueue:
    def __init__(self) -> None:
        self.messages: list[QueueMessage] = []

    def enqueue(self, topic: str, payload: dict[str, Any]) -> QueueMessage:
        message = QueueMessage(topic=topic, payload=payload)
        self.messages.append(message)
        return message
