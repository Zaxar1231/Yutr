"""Command line interface: ``autodub dub URL``, ``autodub system``, ``autodub doctor``."""

from __future__ import annotations

import argparse
import json
import logging
import signal
import sys
from pathlib import Path

from autodub import __version__
from autodub.core.config import load_config
from autodub.core.errors import UserFacingError
from autodub.core.logs import setup_logging
from autodub.core.paths import resolve_paths
from autodub.hardware.guard import ResourceGuard
from autodub.hardware.profiles import Tier
from autodub.hardware.service import HardwareService, bug_report_summary
from autodub.pipeline.context import Cancelled, Control, JobContext, JobOptions
from autodub.pipeline.models import MixMode, Quality
from autodub.pipeline.runner import Progress, Runner
from autodub.pipeline.stages import AlreadyRussian
from autodub.runtime import build_runtime

log = logging.getLogger("autodub")


def _section(text: str) -> tuple[float, float]:
    a, b = text.split("-", 1)
    return float(a), float(b)


def _fmt_eta(s: float | None) -> str:
    if s is None:
        return ""
    m, sec = divmod(int(s), 60)
    return f" · осталось ~{m} мин {sec:02d} с" if m else f" · осталось ~{sec} с"


def cmd_dub(args: argparse.Namespace) -> int:
    paths = resolve_paths(Path(args.data) if args.data else None).ensure()
    setup_logging(paths.logs, level=logging.DEBUG if args.verbose else logging.INFO, console=args.verbose)
    cfg = load_config(paths.config_file)
    hw = HardwareService(paths=paths, config=cfg)
    if hw.rescan_reason():
        print("Определяю железо…", flush=True)
        hw.scan()
    if args.profile:
        cfg.hardware.forced_tier = args.profile
    sel = hw.selection()
    print(f"Профиль: {sel.tier.label} ({sel.accelerator.value})", flush=True)
    for w in sel.warnings:
        print(f"! {w}", flush=True)
    rt = build_runtime(paths)
    opts = JobOptions(
        url=args.url,
        quality=Quality(args.quality),
        mode=MixMode(args.mode),
        original_volume=args.original_volume,
        translator=args.translator,
        tts_engine=args.tts,
        diarize=args.diarize,
        num_speakers=args.speakers,
        cookies_from_browser=args.cookies_from_browser,
        section=_section(args.section) if args.section else None,
        output_dir=str(Path(args.out).resolve()),
        force_translate=args.force,
    )
    guard = ResourceGuard(learned_peaks=hw.state.learned_peaks)
    control = Control()
    signal.signal(signal.SIGINT, lambda *_: control.cancel.set())
    last = {"stage": ""}

    def on_progress(p: Progress) -> None:
        if p.stage != last["stage"]:
            last["stage"] = p.stage
            print(f"\n[{p.stage_index + 1}/{p.stage_count}] {p.stage_title}", flush=True)
        bar = int(p.stage_fraction * 20)
        sys.stdout.write(
            f"\r  {'█' * bar}{'░' * (20 - bar)} {p.stage_fraction * 100:5.1f}%  "
            f"всего {p.overall * 100:4.1f}%{_fmt_eta(p.eta_seconds)}   "
        )
        sys.stdout.flush()

    ctx = JobContext(
        opts,
        rt,
        sel,
        guard=guard,
        progress=None,
        notify=lambda m: print(f"\nℹ {m}", flush=True),
        control=control,
    )
    try:
        result = Runner(ctx, hw.reselect, on_progress).run()
    except AlreadyRussian as ar:
        print(
            f"\nВидео уже на русском (уверенность {ar.verdict.confidence:.0%}). "
            "Перевод не нужен. Чтобы всё равно перевести, добавьте --force."
        )
        return 0
    except Cancelled:
        print("\nОтменено. Повторный запуск продолжит с того же места.")
        return 130
    except UserFacingError as e:
        log.error("job failed: %s | %s", e, e.detail)
        print(f"\nОшибка: {e}")
        return 1
    finally:
        hw.record_peaks(guard.learned_peaks)
    fit = result.details["fit"]
    print(f"\n\nГотово: {result.output}")
    print(f"Субтитры: {result.details['srt_ru']}")
    print(
        f"Ускорено фраз: {fit['sped_up']}, сокращено переводчиком: {len(fit['shortened'])}, "
        f"обрезано: {len(fit['truncated'])}"
    )
    return 0


def cmd_system(args: argparse.Namespace) -> int:
    paths = resolve_paths().ensure()
    setup_logging(paths.logs, console=False)
    hw = HardwareService(paths=paths, config=load_config(paths.config_file))
    if args.rescan or hw.rescan_reason():
        hw.scan()
    sel = hw.selection()
    lo, hi = hw.estimate_10min()
    report = hw.state.report
    assert report is not None
    if args.json:
        print(
            json.dumps(
                {"report": report.model_dump(), "selection": sel.model_dump()},
                ensure_ascii=False,
                indent=1,
                default=str,
            )
        )
        return 0
    print(f"Профиль: {sel.tier.label}  ·  ускоритель: {sel.accelerator.value}")
    for r in sel.reasons:
        print(f"  • {r}")
    for w in sel.warnings:
        print(f"  ! {w}")
    print(f"10 минут видео ≈ {lo}–{hi} мин")
    for k, v in sel.settings.model_dump().items():
        print(f"  {k:32} {v}")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    paths = resolve_paths().ensure()
    hw = HardwareService(paths=paths, config=load_config(paths.config_file))
    if hw.rescan_reason():
        hw.scan()
    assert hw.state.report is not None
    summary = bug_report_summary(hw.state.report, hw.selection(), __version__)
    rt = build_runtime(paths)
    summary["engine_python"] = bool(rt.engine_python)
    summary["ffmpeg"] = rt.tools.ffmpeg
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="autodub", description="Автодубляж видео на русский")
    ap.add_argument("--version", action="version", version=__version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("dub", help="дублировать видео по ссылке или файлу")
    d.add_argument("url")
    d.add_argument("--out", default=".", help="папка для результата")
    d.add_argument("--quality", choices=[q.value for q in Quality], default="best")
    d.add_argument("--mode", choices=[m.value for m in MixMode], default="dub")
    d.add_argument("--original-volume", type=float, default=0.2)
    d.add_argument("--translator", choices=["claude", "madlad"], default="claude")
    d.add_argument("--tts", choices=["xtts_v2", "f5_tts_ru"], default=None)
    d.add_argument("--diarize", action="store_true", help="разные голоса для разных спикеров")
    d.add_argument("--speakers", type=int, default=None)
    d.add_argument("--cookies-from-browser", default=None)
    d.add_argument("--section", default=None, help="только часть видео, например 60-150")
    d.add_argument("--profile", choices=[t.value for t in Tier], default=None)
    d.add_argument("--force", action="store_true", help="переводить даже русское видео")
    d.add_argument("--data", default=None, help="папка данных (портативный режим)")
    d.add_argument("-v", "--verbose", action="store_true")
    d.set_defaults(func=cmd_dub)

    s = sub.add_parser("system", help="показать железо и профиль")
    s.add_argument("--rescan", action="store_true")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_system)

    doc = sub.add_parser("doctor", help="отчёт для сообщения об ошибке")
    doc.add_argument("--report", action="store_true")
    doc.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
