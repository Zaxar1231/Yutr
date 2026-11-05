"""Semantic Versioning 2.0 parsing and precedence (tags like ``v1.2.3-beta.1``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

_RE = re.compile(
    r"^v?(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


@total_ordering
@dataclass(frozen=True)
class Version:
    major: int
    minor: int
    patch: int
    pre: tuple[str, ...] = ()

    @classmethod
    def parse(cls, text: str) -> Version:
        m = _RE.match(text.strip())
        if not m:
            raise ValueError(f"not a semantic version: {text!r}")
        pre = tuple(m.group("pre").split(".")) if m.group("pre") else ()
        return cls(int(m["major"]), int(m["minor"]), int(m["patch"]), pre)

    @property
    def prerelease(self) -> bool:
        return bool(self.pre)

    def _pre_key(self) -> tuple[object, ...]:
        # A release has higher precedence than any of its pre-releases.
        if not self.pre:
            return (1,)
        parts: list[tuple[int, int | str]] = []
        for p in self.pre:
            parts.append((0, int(p)) if p.isdigit() else (1, p))
        return (0, *parts)

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, Version):
            return NotImplemented
        a = (self.major, self.minor, self.patch)
        b = (other.major, other.minor, other.patch)
        if a != b:
            return a < b
        return self._pre_key() < other._pre_key()

    def __str__(self) -> str:
        s = f"{self.major}.{self.minor}.{self.patch}"
        return s + ("-" + ".".join(self.pre) if self.pre else "")


def from_pep440(text: str) -> Version:
    """Map a Python package version (0.1.0, 0.1.0b1, 0.1.0.dev0) onto semver."""
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:\.?(a|b|rc|dev)(\d+))?", text)
    if not m:
        raise ValueError(text)
    pre: tuple[str, ...] = ()
    if m.group(4):
        label = {"a": "alpha", "b": "beta", "rc": "rc", "dev": "dev"}[m.group(4)]
        pre = (label, m.group(5))
    return Version(int(m[1]), int(m[2]), int(m[3]), pre)
