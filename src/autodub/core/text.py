"""Small helpers for Russian UI text."""

from __future__ import annotations


def plural(n: int, one: str, few: str, many: str) -> str:
    """Russian plural form: 1 ядро, 2 ядра, 5 ядер, 21 ядро, 11 ядер."""
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def count(n: int, one: str, few: str, many: str) -> str:
    return f"{n} {plural(n, one, few, many)}"
