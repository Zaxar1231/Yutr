"""Release discovery through the GitHub REST API.

Rate limits: unauthenticated clients get 60 requests/hour per IP. The checker stores the
ETag of the last response and sends ``If-None-Match``; a 304 answer does not count against
the limit. Checks happen at start and then every N hours (configurable).
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from autodub.updater.semver import Version

log = logging.getLogger(__name__)
API = os.environ.get("AUTODUB_GITHUB_API", "https://api.github.com")


@dataclass
class Release:
    version: Version
    tag: str
    name: str
    notes: str  # Markdown changelog from the release body
    prerelease: bool
    assets: dict[str, str]  # name -> browser_download_url
    published_at: str

    def asset(self, suffix: str) -> tuple[str, str] | None:
        for name, url in self.assets.items():
            if name.endswith(suffix):
                return name, url
        return None


Fetcher = Callable[[str, dict[str, str]], tuple[int, dict[str, str], bytes]]


def http_fetch(url: str, headers: dict[str, str]) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() if e.fp else b""


def parse_releases(data: list[dict[str, Any]]) -> list[Release]:
    out = []
    for r in data:
        if r.get("draft"):
            continue
        try:
            v = Version.parse(r["tag_name"])
        except ValueError:
            continue
        out.append(
            Release(
                version=v,
                tag=r["tag_name"],
                name=r.get("name") or r["tag_name"],
                notes=r.get("body") or "",
                prerelease=bool(r.get("prerelease")) or v.prerelease,
                assets={a["name"]: a["browser_download_url"] for a in r.get("assets", [])},
                published_at=r.get("published_at") or "",
            )
        )
    return out


def pick_update(
    releases: list[Release],
    current: Version,
    *,
    channel: str,
    skipped: Collection[str] = (),
    bad: Collection[str] = (),
) -> Release | None:
    """Newest release above ``current`` allowed by the channel, not skipped and not known bad."""
    allowed = [
        r
        for r in releases
        if r.version > current
        and (channel == "beta" or not r.prerelease)
        and str(r.version) not in skipped
        and str(r.version) not in bad
    ]
    return max(allowed, key=lambda r: r.version, default=None)


class UpdateChecker:
    def __init__(
        self, repo: str, state_file: Path, *, fetch: Fetcher = http_fetch, user_agent: str = "Autodub-Updater"
    ):
        self.repo = repo
        self.state_file = state_file
        self.fetch = fetch
        self.ua = user_agent
        self.state: dict[str, Any] = json.loads(state_file.read_text("utf-8")) if state_file.exists() else {}

    def _save(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(json.dumps(self.state, ensure_ascii=False), "utf-8")

    def due(self, interval_hours: float, now: float | None = None) -> bool:
        last = float(self.state.get("last_check", 0))
        return (now or time.time()) - last >= interval_hours * 3600

    def releases(self) -> list[Release]:
        url = f"{API}/repos/{self.repo}/releases?per_page=20"
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self.ua,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self.state.get("etag"):
            headers["If-None-Match"] = self.state["etag"]
        status, resp_headers, body = self.fetch(url, headers)
        self.state["last_check"] = time.time()
        if status == 304 and "cache" in self.state:
            data = self.state["cache"]
        elif status == 200:
            data = json.loads(body)
            self.state["cache"] = data
            etag = {k.lower(): v for k, v in resp_headers.items()}.get("etag")
            if etag:
                self.state["etag"] = etag
        else:
            log.warning("GitHub releases request failed: %s", status)
            self._save()
            return []
        self._save()
        return parse_releases(data)

    def check(self, current: Version, *, channel: str, bad: Collection[str] = ()) -> Release | None:
        skipped = set(self.state.get("skipped", []))
        return pick_update(self.releases(), current, channel=channel, skipped=skipped, bad=bad)

    def skip(self, version: str) -> None:
        self.state.setdefault("skipped", [])
        if version not in self.state["skipped"]:
            self.state["skipped"].append(version)
        self._save()
