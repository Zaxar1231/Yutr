"""Мастер первого запуска: железо → лицензии → что скачать (с размерами) → установка → калибровка."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import flet as ft

from autodub.components import manifest
from autodub.components.envs import python_path
from autodub.components.manager import ComponentManager
from autodub.hardware.calibrate import calibration_audio_path, run_calibration
from autodub.hardware.models import Accelerator
from autodub.hardware.profiles import ProfileSettings
from autodub.hardware.service import HardwareService

log = logging.getLogger(__name__)

ENV_SIZE_MB = {
    Accelerator.CUDA: 5200,
    Accelerator.ROCM: 5000,
    Accelerator.DIRECTML: 1600,
    Accelerator.CPU: 1600,
}


@dataclass
class Step:
    key: str
    title: str
    size_mb: float
    run: Callable[[Callable[[float, str], None]], Any]


def asr_probe_factory(models_dir: Any) -> Callable[[ProfileSettings], float | None]:
    def probe(settings: ProfileSettings) -> float | None:
        audio = calibration_audio_path()
        if not audio.exists():
            return None
        from faster_whisper import WhisperModel

        local = models_dir / f"whisper-{settings.whisper_model}"
        model = WhisperModel(
            str(local) if local.exists() else settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
            cpu_threads=settings.cpu_threads,
        )
        import soundfile as sf

        data, sr = sf.read(str(audio), dtype="float32")
        t0 = time.perf_counter()
        segs, _ = model.transcribe(data, language="en", beam_size=5)
        list(segs)
        return float((time.perf_counter() - t0) / (len(data) / sr))

    return probe


class Wizard(ft.Column):
    def __init__(
        self,
        hw: HardwareService,
        manager: ComponentManager,
        finish: Callable[[], None],
        save_config: Callable[[], None],
    ):
        super().__init__(
            spacing=20,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.hw, self.manager, self.finish, self.save_config = hw, manager, finish, save_config
        self.body = ft.Column(spacing=16, width=720)
        self.controls = [self.body]
        self.steps: list[Step] = []
        self.show_intro()

    # --- pages ----------------------------------------------------------------------------

    def _page(self, title: str, subtitle: str, content: list[ft.Control], actions: list[ft.Control]) -> None:
        self.body.controls = [
            ft.Container(height=24),
            ft.Icon(ft.Icons.RECORD_VOICE_OVER, size=56, color=ft.Colors.PRIMARY),
            ft.Text(title, theme_style=ft.TextThemeStyle.HEADLINE_MEDIUM),
            ft.Text(subtitle, color=ft.Colors.ON_SURFACE_VARIANT),
            *content,
            ft.Row(actions, alignment=ft.MainAxisAlignment.END),
        ]
        if self.page:
            self.update()

    def show_intro(self) -> None:
        self._page(
            "Добро пожаловать в Автодубляж",
            "Сейчас программа определит ваш компьютер и сама скачает всё нужное. "
            "Ничего настраивать вручную не придётся.",
            [],
            [ft.FilledButton(content="Начать", icon=ft.Icons.ARROW_FORWARD, on_click=lambda _e: self.scan())],
        )

    def scan(self) -> None:
        ring = ft.ProgressRing()
        self._page(
            "Определяю железо…",
            "Видеокарта, процессор, память, диски и скорость интернета.",
            [ft.Row([ring], alignment=ft.MainAxisAlignment.CENTER)],
            [],
        )
        assert self.page is not None

        def work() -> None:
            try:
                self.hw.scan()
            except Exception:
                log.exception("scan failed")
            self.show_plan()

        self.page.run_thread(work)

    def _model_installer(self, key: str) -> Callable[[Callable[[float, str], None]], Any]:
        return lambda p: self.manager.install_model(key, p)

    def _binary_installer(self, key: str) -> Callable[[Callable[[float, str], None]], Any]:
        return lambda p: self.manager.install_binary(key, p)

    def _plan_steps(self) -> list[Step]:
        sel = self.hw.selection()
        report = self.hw.state.report
        d = self.hw.config.defaults
        models = manifest.models_for(
            sel.settings, offline_translation=d.translator == "madlad", tts_engine=d.tts_engine
        )
        steps: list[Step] = []
        if not python_path(self.manager.paths.envs).exists():
            steps.append(
                Step(
                    "env",
                    "PyTorch и движки нейросетей",
                    ENV_SIZE_MB[sel.accelerator],
                    lambda p: self.manager.install_env(report, sel.accelerator, p),
                )
            )
        for key, b in manifest.binaries().items():
            steps.append(Step(key, b.title, b.size_mb, self._binary_installer(key)))
        for key in models:
            try:
                size = self.manager.model_size_mb(key)
            except Exception:
                size = 0
            steps.append(Step(key, manifest.models()[key].title, size, self._model_installer(key)))
        return steps

    def show_plan(self) -> None:
        sel = self.hw.selection()
        self.steps = self._plan_steps()
        total = sum(s.size_mb for s in self.steps)
        lo, hi = self.hw.estimate_10min()
        nc = [
            manifest.models()[s.key]
            for s in self.steps
            if s.key in manifest.models() and manifest.models()[s.key].noncommercial
        ]
        accept = ft.Checkbox(
            label="Я понимаю, что эти модели можно использовать только некоммерчески",
            value=not nc,
        )
        rows = [
            ft.Row(
                [
                    ft.Text(s.title, expand=True),
                    ft.Text(
                        f"{s.size_mb / 1024:.1f} ГБ" if s.size_mb >= 1024 else f"{s.size_mb:.0f} МБ",
                        color=ft.Colors.ON_SURFACE_VARIANT,
                    ),
                ]
            )
            for s in self.steps
        ]
        go = ft.FilledButton(
            content=f"Скачать ({total / 1024:.1f} ГБ)",
            icon=ft.Icons.DOWNLOAD,
            on_click=lambda _e: self.install(),
            disabled=bool(nc),
        )

        def on_accept(e: Any) -> None:
            go.disabled = not e.control.value
            go.update()

        accept.on_change = on_accept
        content: list[ft.Control] = [
            ft.Card(
                variant=ft.CardVariant.FILLED,
                bgcolor=ft.Colors.PRIMARY_CONTAINER,
                content=ft.Container(
                    padding=16,
                    content=ft.Column(
                        [
                            ft.Text(
                                f"Профиль: {sel.tier.label}",
                                theme_style=ft.TextThemeStyle.TITLE_LARGE,
                                color=ft.Colors.ON_PRIMARY_CONTAINER,
                            ),
                            *[ft.Text(f"• {r}", color=ft.Colors.ON_PRIMARY_CONTAINER) for r in sel.reasons],
                            ft.Text(f"10 минут видео ≈ {lo}–{hi} мин", color=ft.Colors.ON_PRIMARY_CONTAINER),
                        ],
                        spacing=4,
                    ),
                ),
            ),
            *[ft.Text(f"⚠ {w}", color=ft.Colors.TERTIARY) for w in sel.warnings],
            ft.Text("Будет скачано только то, что нужно вашему профилю:", weight=ft.FontWeight.W_600),
            *rows,
            ft.Divider(),
            ft.Row(
                [
                    ft.Text("Всего", weight=ft.FontWeight.W_600, expand=True),
                    ft.Text(f"{total / 1024:.1f} ГБ", weight=ft.FontWeight.W_600),
                ]
            ),
        ]
        if nc:
            content += [
                ft.Text(
                    "Лицензии моделей озвучки: " + "; ".join(f"{m.title} — {m.license}" for m in nc), size=12
                ),
                accept,
            ]
        self._page(
            "Что будет установлено",
            "Загрузка продолжится с места обрыва, если пропадёт интернет.",
            content,
            [go],
        )

    def install(self) -> None:
        bars = {s.key: ft.ProgressBar(value=0, border_radius=4) for s in self.steps}
        labels = {s.key: ft.Text("", size=12, color=ft.Colors.ON_SURFACE_VARIANT) for s in self.steps}
        content: list[ft.Control] = []
        for s in self.steps:
            content += [ft.Text(s.title, weight=ft.FontWeight.W_500), bars[s.key], labels[s.key]]
        self._page("Устанавливаю компоненты", "Можно свернуть окно — всё продолжится в фоне.", content, [])
        self.hw.config.licenses_accepted = sorted({s.key for s in self.steps if s.key in manifest.models()})
        assert self.page is not None

        def work() -> None:
            failed = []
            for s in self.steps:

                def progress(frac: float, msg: str, _k: str = s.key) -> None:
                    bars[_k].value = frac
                    labels[_k].value = msg[:90]
                    self.update()

                try:
                    s.run(progress)
                    bars[s.key].value = 1
                    labels[s.key].value = "Готово"
                except Exception as exc:
                    log.exception("install %s failed", s.key)
                    failed.append(s.title)
                    labels[s.key].value = f"Ошибка: {exc}"
                    bars[s.key].color = ft.Colors.ERROR
                self.update()
            if failed:
                self._page(
                    "Не всё удалось скачать",
                    "Проверьте интернет и нажмите «Повторить». Уже скачанное не потеряется.",
                    [ft.Text("• " + f) for f in failed],
                    [ft.FilledButton(content="Повторить", on_click=lambda _e: self.install())],
                )
                return
            self.calibrate()

        self.page.run_thread(work)

    def _cal_progress(self, bar: ft.ProgressBar, text: ft.Text) -> Callable[[str, float], None]:
        def report(message: str, frac: float) -> None:
            bar.value = frac
            text.value = message
            self.update()

        return report

    def calibrate(self) -> None:
        bar = ft.ProgressBar(value=0, border_radius=4)
        text = ft.Text("")
        self._page(
            "Калибровка (~30 секунд)",
            "Короткий тест, чтобы точнее подобрать настройки под ваш ПК.",
            [bar, text],
            [],
        )
        sel = self.hw.selection()
        report = self.hw.state.report
        assert report is not None
        try:
            cal = run_calibration(
                sel,
                fingerprint=report.fingerprint(),
                engine_python=str(python_path(self.manager.paths.envs)),
                asr_probe=asr_probe_factory(self.manager.paths.models),
                progress=self._cal_progress(bar, text),
            )
            self.hw.set_calibration(cal)
        except Exception:
            log.exception("calibration failed")
        self.hw.config.first_run_done = True
        self.save_config()
        sel = self.hw.selection()
        lo, hi = self.hw.estimate_10min()
        self._page(
            "Всё готово!",
            f"Профиль «{sel.tier.label}». Видео на 10 минут — примерно {lo}–{hi} мин.",
            [ft.Text(r) for r in sel.reasons[-3:]],
            [
                ft.FilledButton(
                    content="Начать работу", icon=ft.Icons.CHECK, on_click=lambda _e: self.finish()
                )
            ],
        )
