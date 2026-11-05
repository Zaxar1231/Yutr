"""API keys and tokens in the OS credential store (Windows Credential Manager via keyring).

Never written to config files. Environment variables are honoured for CLI/CI use.
"""

from __future__ import annotations

import contextlib
import logging
import os

log = logging.getLogger(__name__)
SERVICE = "Autodub"
ENV = {"anthropic_api_key": "ANTHROPIC_API_KEY", "hf_token": "HF_TOKEN"}


def get_secret(name: str) -> str | None:
    env = ENV.get(name)
    if env and os.environ.get(env):
        return os.environ[env]
    try:
        import keyring

        return keyring.get_password(SERVICE, name)
    except Exception as exc:  # no backend (headless Linux) etc.
        log.info("keyring unavailable: %s", exc)
        return None


def set_secret(name: str, value: str | None) -> None:
    import keyring

    if value:
        keyring.set_password(SERVICE, name, value)
    else:
        with contextlib.suppress(keyring.errors.PasswordDeleteError):
            keyring.delete_password(SERVICE, name)
