"""Errors that are safe to show to the user.

Everything the UI displays goes through :class:`UserFacingError`: the message is plain
Russian, while technical details stay in the log file.
"""

from __future__ import annotations


class UserFacingError(Exception):
    """An error with a message written for the user, not for a developer."""

    def __init__(self, message: str, *, hint: str | None = None, detail: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.detail = detail  # technical detail, goes to the log only

    def __str__(self) -> str:
        return self.message if not self.hint else f"{self.message} {self.hint}"
