"""The LLM provider interface (section 3.5). Swapping providers is one file."""

from __future__ import annotations

from typing import Protocol

from ...schemas import Answer, Passage


class LLMProvider(Protocol):
    name: str

    def generate(self, question: str, passages: list[Passage],
                 problem: str | None = None) -> Answer:
        """Answer the question from the passages, as an `Answer`.

        `problem` is set on the one retry: why the previous reply was rejected. Raise
        ValueError for a reply that is not a valid Answer, RuntimeError when the model
        cannot be reached."""
        ...
