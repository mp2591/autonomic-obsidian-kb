from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .util import estimate_tokens


@dataclass(slots=True)
class TokenCount:
    tokens: int
    exact: bool
    profile: str


class TokenizerRegistry:
    """Explicit tokenizer adapter. The built-in estimator is marked non-exact."""

    def __init__(self):
        self._adapters: dict[str, Callable[[str], int]] = {}

    def register(self, profile: str, counter: Callable[[str], int]) -> None:
        self._adapters[profile.lower()] = counter

    def count(self, text: str, profile: str = "generic") -> TokenCount:
        adapter = self._adapters.get(profile.lower())
        if adapter is not None:
            return TokenCount(max(0, int(adapter(text))), True, profile)
        return TokenCount(estimate_tokens(text, profile), False, profile)


TOKENIZERS = TokenizerRegistry()
