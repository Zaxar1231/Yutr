import pytest

from autodub.updater.semver import Version, from_pep440


@pytest.mark.parametrize(
    "lower,higher",
    [
        ("0.1.0", "0.1.1"),
        ("v0.9.9", "v0.10.0"),
        ("1.0.0-alpha", "1.0.0-alpha.1"),
        ("1.0.0-alpha.1", "1.0.0-alpha.beta"),
        ("1.0.0-alpha.beta", "1.0.0-beta"),
        ("1.0.0-beta", "1.0.0-beta.2"),
        ("1.0.0-beta.2", "1.0.0-beta.11"),  # numeric, not lexical
        ("1.0.0-beta.11", "1.0.0-rc.1"),
        ("1.0.0-rc.1", "1.0.0"),
        ("1.9.0", "2.0.0"),
    ],
)
def test_precedence(lower: str, higher: str) -> None:
    assert Version.parse(lower) < Version.parse(higher)


def test_build_metadata_ignored_and_str() -> None:
    assert Version.parse("1.2.3+build.5") == Version.parse("1.2.3")
    assert str(Version.parse("v1.2.3-rc.1")) == "1.2.3-rc.1"


@pytest.mark.parametrize("bad", ["1.2", "01.2.3", "1.2.3-", "latest", "v1.2.3.4"])
def test_invalid(bad: str) -> None:
    with pytest.raises(ValueError):
        Version.parse(bad)


def test_pep440() -> None:
    assert from_pep440("0.1.0") == Version(0, 1, 0)
    assert from_pep440("0.2.0b1") == Version.parse("0.2.0-beta.1")
    assert from_pep440("0.1.0.dev0") < Version.parse("0.1.0")
