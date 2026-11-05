import json

from autodub.updater.github import UpdateChecker, parse_releases, pick_update
from autodub.updater.semver import Version

RELEASES = [
    {"tag_name": "v0.2.0-beta.1", "prerelease": True, "body": "beta", "assets": []},
    {
        "tag_name": "v0.1.1",
        "prerelease": False,
        "body": "## Исправлено\n- баг",
        "assets": [
            {"name": "Autodub-0.1.1-update.zip", "browser_download_url": "https://x/u.zip"},
            {"name": "checksums.txt", "browser_download_url": "https://x/c.txt"},
        ],
    },
    {"tag_name": "v0.1.0", "prerelease": False, "body": "", "assets": []},
    {"tag_name": "nightly", "prerelease": True, "body": "", "assets": []},
    {"tag_name": "v9.9.9", "draft": True, "assets": []},
]


def test_channels() -> None:
    rel = parse_releases(RELEASES)
    cur = Version.parse("0.1.0")
    assert str(pick_update(rel, cur, channel="stable").version) == "0.1.1"
    assert str(pick_update(rel, cur, channel="beta").version) == "0.2.0-beta.1"
    assert pick_update(rel, Version.parse("0.1.1"), channel="stable") is None


def test_skip_and_bad_versions() -> None:
    rel = parse_releases(RELEASES)
    cur = Version.parse("0.1.0")
    assert pick_update(rel, cur, channel="stable", skipped={"0.1.1"}) is None
    assert str(pick_update(rel, cur, channel="beta", bad={"0.2.0-beta.1"}).version) == "0.1.1"


def test_etag_conditional_requests(tmp_path) -> None:
    calls = []

    def fetch(url, headers):
        calls.append(headers.copy())
        if headers.get("If-None-Match") == '"abc"':
            return 304, {}, b""
        return 200, {"ETag": '"abc"'}, json.dumps(RELEASES).encode()

    ch = UpdateChecker("o/r", tmp_path / "upd.json", fetch=fetch)
    first = ch.check(Version.parse("0.1.0"), channel="stable")
    ch2 = UpdateChecker("o/r", tmp_path / "upd.json", fetch=fetch)  # new process
    second = ch2.check(Version.parse("0.1.0"), channel="stable")
    assert first and second and first.tag == second.tag == "v0.1.1"
    assert "If-None-Match" not in calls[0] and calls[1]["If-None-Match"] == '"abc"'
    assert "Исправлено" in second.notes
    ch2.skip("0.1.1")
    assert ch2.check(Version.parse("0.1.0"), channel="stable") is None


def test_interval(tmp_path) -> None:
    ch = UpdateChecker("o/r", tmp_path / "s.json", fetch=lambda u, h: (500, {}, b""))
    assert ch.due(12)
    assert ch.releases() == []  # API failure is not an error
    assert not ch.due(12)
    assert ch.due(12, now=ch.state["last_check"] + 13 * 3600)
