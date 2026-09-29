"""Erreurs du métier, chacune avec son code d'API (voir docs/extension/API.md)."""
from __future__ import annotations


class SheldonError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code
