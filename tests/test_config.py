from __future__ import annotations

from pathlib import Path

from autodub.core import config as cfgmod
from autodub.core.text import plural


def test_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    cfg = cfgmod.AppConfig()
    cfg.hardware.overrides["whisper_batch_size"] = 2
    cfgmod.save_config(cfg, p)
    assert cfgmod.load_config(p).hardware.overrides == {"whisper_batch_size": 2}


def test_broken_config_is_backed_up(tmp_path: Path) -> None:
    p = tmp_path / "config.toml"
    p.write_text("this is = = not toml")
    assert cfgmod.load_config(p) == cfgmod.AppConfig()
    assert (tmp_path / "config.broken.toml").exists()


def test_migrations_run_in_order(monkeypatch) -> None:
    monkeypatch.setattr(cfgmod, "SCHEMA_VERSION", 3)
    monkeypatch.setattr(
        cfgmod,
        "MIGRATIONS",
        {
            1: lambda raw: raw | {"theme": "dark"},
            2: lambda raw: raw | {"output_dir": "D:/out"},
        },
    )
    out = cfgmod.migrate({"schema_version": 1})
    assert out == {"schema_version": 3, "theme": "dark", "output_dir": "D:/out"}


def test_plural() -> None:
    forms = ("ядро", "ядра", "ядер")
    assert [plural(n, *forms) for n in (1, 2, 5, 11, 12, 21, 22, 25, 111)] == [
        "ядро",
        "ядра",
        "ядер",
        "ядер",
        "ядер",
        "ядро",
        "ядра",
        "ядер",
        "ядер",
    ]
