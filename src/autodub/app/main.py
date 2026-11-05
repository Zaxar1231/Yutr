"""Application shell: navigation, theme, queue, updates, first-run wizard."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import flet as ft

from autodub import __version__
from autodub.app.queue import Job, JobQueue
from autodub.app.screens.about import AboutScreen
from autodub.app.screens.components import ComponentsScreen
from autodub.app.screens.home import HomeScreen
from autodub.app.screens.result import ResultScreen
from autodub.app.screens.settings import SettingsScreen
from autodub.app.screens.system import SystemScreen
from autodub.app.screens.wizard import Wizard
from autodub.app.theme import apply_theme
from autodub.components import manifest
from autodub.components.manager import ComponentManager
from autodub.core.config import load_config, save_config
from autodub.core.logs import setup_logging
from autodub.core.paths import resolve_paths
from autodub.hardware.guard import check_disk
from autodub.hardware.models import HardwareReport
from autodub.hardware.service import HardwareService, bug_report_summary
from autodub.updater.github import Release
from autodub.updater.service import UpdateService

log = logging.getLogger(__name__)
ASSETS = Path(__file__).resolve().parent / "assets"

DESTINATIONS = [
    (ft.Icons.HOME_OUTLINED, ft.Icons.HOME, "Главная"),
    (ft.Icons.MEMORY_OUTLINED, ft.Icons.MEMORY, "Моя система"),
    (ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS, "Настройки"),
    (ft.Icons.EXTENSION_OUTLINED, ft.Icons.EXTENSION, "Компоненты"),
    (ft.Icons.INFO_OUTLINE, ft.Icons.INFO, "О программе"),
]


def build_service(paths: Any, config: Any) -> HardwareService:
    demo = os.environ.get("AUTODUB_DEMO_HARDWARE")
    if demo:  # scripts/screenshots.py shows a fixed demo machine
        report = HardwareReport.model_validate_json(Path(demo).read_text("utf-8"))
        return HardwareService(
            paths=paths,
            config=config,
            scan_fn=lambda **_: report.model_copy(deep=True),
            fingerprint_fn=report.fingerprint,
        )
    return HardwareService(paths=paths, config=config)


class App:
    def __init__(self, page: ft.Page):
        self.page = page
        self.paths = resolve_paths().ensure()
        setup_logging(self.paths.logs, console=False)
        self.config = load_config(self.paths.config_file)
        self.hw = build_service(self.paths, self.config)
        self.components = ComponentManager(self.paths)
        self.updates = UpdateService(self.config, self.paths.root)
        if self.config.first_run_done and self.hw.rescan_reason() == "first_run":
            self.hw.scan()  # state file lost: rebuild it before the screens read it
        page.title = f"Автодубляж {__version__}"
        page.padding = 0
        page.window.min_width, page.window.min_height = 900, 640
        apply_theme(page, self.config.theme)
        self.queue = JobQueue(self.paths.root / "queue.json", self._job_changed)

        self.home = HomeScreen(
            self.config, self.queue, self.output_dir, self.open_result, self.notify, self.disk_warning
        )
        self.system = SystemScreen(self.hw, self.notify)
        self.settings = SettingsScreen(
            self.config, self.save, self.notify, self._theme, lambda: str(self.output_dir())
        )
        self.components_screen = ComponentsScreen(
            self.components, self.needed_models, self.repair, self.notify
        )
        self.about = AboutScreen(
            self.config.updates.repository,
            lambda: self.check_updates(force=True),
            self.bug_summary,
            self.open_url,
        )
        self.result = ResultScreen(back=lambda: self.go(0))
        self.screens: list[ft.Control] = [
            self.home,
            self.system,
            self.settings,
            self.components_screen,
            self.about,
        ]
        self.body = ft.Container(
            expand=True, padding=24, animate_opacity=ft.Animation(200, ft.AnimationCurve.EASE_OUT)
        )
        self.rail = ft.NavigationRail(
            selected_index=0,
            label_type=ft.NavigationRailLabelType.ALL,
            min_width=88,
            destinations=[
                ft.NavigationRailDestination(icon=i, selected_icon=si, label=t) for i, si, t in DESTINATIONS
            ],
            on_change=lambda e: self.go(e.control.selected_index or 0),
        )
        self.banner_slot = ft.Column(spacing=0)
        self.shell = ft.Row(
            [
                self.rail,
                ft.VerticalDivider(width=1),
                ft.Column([self.banner_slot, self.body], expand=True, spacing=0),
            ],
            expand=True,
            spacing=0,
        )

    # --- navigation -----------------------------------------------------------------------

    def start(self) -> None:
        names = ["home", "system", "settings", "components", "about"]
        wanted = os.environ.get("AUTODUB_START_SCREEN", "home")
        if not self.config.first_run_done and not os.environ.get("AUTODUB_DEMO_HARDWARE"):
            self.page.add(
                ft.Container(
                    Wizard(self.hw, self.components, self._wizard_done, self.save), expand=True, padding=24
                )
            )
        else:
            self.page.add(self.shell)
            self.go(names.index(wanted) if wanted in names else 0)
            if wanted == "result":
                done = next((j for j in self.queue.jobs if j.status == "done"), None)
                if done:
                    self.open_result(done)
            if not os.environ.get("AUTODUB_DEMO_HARDWARE"):
                self._startup_checks()
        self.updates.mark_started()

    def _wizard_done(self) -> None:
        self.page.controls.clear()
        self.page.add(self.shell)
        self.go(0)
        self._startup_checks()

    def go(self, index: int) -> None:
        self.rail.selected_index = index
        self.body.content = self.screens[index]
        if index == 3:
            self.components_screen.refresh(deep=False)
        self.page.update()

    def open_result(self, job: Job) -> None:
        if not job.output or not Path(job.output).exists():
            self.notify("Файл результата не найден — возможно, его переместили.")
            return
        self.result.show(job)
        self.body.content = self.result
        self.page.update()

    # --- helpers --------------------------------------------------------------------------

    def notify(self, text: str) -> None:
        self.page.show_dialog(ft.SnackBar(ft.Text(text)))

    def save(self) -> None:
        save_config(self.config, self.paths.config_file)

    def _theme(self, mode: str) -> None:
        apply_theme(self.page, mode)
        self.page.update()

    def output_dir(self) -> Path:
        return Path(self.config.output_dir) if self.config.output_dir else self.paths.default_output()

    def disk_warning(self) -> str | None:
        try:
            check_disk(self.output_dir(), 3000, what="видео")
            check_disk(self.paths.cache, 3000, what="временных файлов")
        except Exception as exc:
            return str(exc)
        return None

    def needed_models(self) -> list[str]:
        if self.hw.state.report is None:
            return []
        d = self.config.defaults
        return manifest.models_for(
            self.hw.selection().settings,
            offline_translation=d.translator == "madlad",
            tts_engine=d.tts_engine,
        )

    def repair(self, progress: Any) -> list[str]:
        sel = self.hw.selection()
        return self.components.repair(self.needed_models(), self.hw.state.report, sel.accelerator, progress)

    def bug_summary(self) -> dict[str, Any]:
        report = self.hw.state.report
        if report is None:
            return {"version": __version__}
        return bug_report_summary(report, self.hw.selection(), __version__)

    def open_url(self, url: str) -> None:
        self.page.run_task(ft.UrlLauncher().launch_url, url)

    def _job_changed(self, job: Job) -> None:
        try:
            self.home.job_changed(job)
        except Exception:  # the UI may be closing
            log.debug("job update ignored", exc_info=True)
        if job.status == "already_russian":
            self.notify("Видео уже на русском — перевод не нужен.")
        elif job.status == "done" and job.overall >= 1:
            self.notify(f"Готово: {job.title}")

    # --- startup: hardware change, components, updates ---------------------------------------

    def _startup_checks(self) -> None:
        def work() -> None:
            reason = self.hw.rescan_reason()
            if reason == "hardware_changed":
                self.notify("Обнаружено новое железо или драйвер — подбираю настройки заново.")
                self.hw.scan()
                self.system.rebuild()
                self.page.update()
            sel = self.hw.selection()
            if sel.suggest_eco:
                self.notify("Ноутбук работает от батареи — можно включить экономный режим в «Моя система».")
            missing = [
                s.title
                for s in self.components.status()
                if not s.installed and (s.kind in ("binary", "data", "env") or s.key in self.needed_models())
            ]
            if missing and not os.environ.get("AUTODUB_DEMO_HARDWARE"):
                self.notify("Не хватает компонентов, докачиваю: " + ", ".join(missing))
                try:
                    self.repair(lambda f, m: None)
                except Exception:
                    log.exception("auto repair failed")
            try:
                log.info("yt-dlp update: %s", self.components.update_ytdlp())
            except Exception:
                log.warning("yt-dlp self-update failed", exc_info=True)
            self.check_updates(force=False)
            self.queue.kick()

        self.page.run_thread(work)

    def check_updates(self, *, force: bool) -> None:
        try:
            rel = self.updates.check(force=force)
        except Exception:
            log.warning("update check failed", exc_info=True)
            if force:
                self.notify("Не удалось проверить обновления. Проверьте интернет.")
            return
        if rel is None:
            if force:
                self.notify(f"У вас последняя версия ({__version__}).")
            return
        if self.config.updates.auto_update and self.updates.can_install():
            self._download(rel, then_restart=False)
            return
        self._show_update_banner(rel)

    def _show_update_banner(self, rel: Release) -> None:
        def changelog(_e: Any) -> None:
            self.page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(f"Что нового в {rel.version}"),
                    content=ft.Container(
                        ft.Markdown(
                            rel.notes or "Описание не указано.",
                            selectable=True,
                            extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
                        ),
                        width=560,
                        height=400,
                    ),
                    actions=[ft.TextButton(content="Закрыть", on_click=lambda _e: self.page.pop_dialog())],
                )
            )

        def close() -> None:
            self.banner_slot.controls.clear()
            self.page.update()

        install = self.updates.can_install()
        self.banner_slot.controls = [
            ft.Container(
                ft.Row(
                    [
                        ft.Icon(ft.Icons.NEW_RELEASES, color=ft.Colors.ON_SECONDARY_CONTAINER),
                        ft.Text(
                            f"Доступна версия {rel.version}",
                            expand=True,
                            weight=ft.FontWeight.W_500,
                            color=ft.Colors.ON_SECONDARY_CONTAINER,
                        ),
                        ft.TextButton(content="Что нового", on_click=changelog),
                        ft.TextButton(
                            content="Пропустить версию", on_click=lambda _e: (self.updates.skip(rel), close())
                        ),
                        ft.TextButton(
                            content="При следующем запуске",
                            visible=install,
                            on_click=lambda _e: (self._download(rel, then_restart=False), close()),
                        ),
                        ft.FilledButton(
                            content="Обновить сейчас" if install else "Скачать",
                            on_click=lambda _e: (
                                (self._download(rel, then_restart=True), close())
                                if install
                                else self.open_url(
                                    f"https://github.com/{self.config.updates.repository}"
                                    f"/releases/tag/{rel.tag}"
                                )
                            ),
                        ),
                    ]
                ),
                bgcolor=ft.Colors.SECONDARY_CONTAINER,
                padding=ft.Padding.symmetric(horizontal=20, vertical=8),
            )
        ]
        self.page.update()

    def _download(self, rel: Release, *, then_restart: bool) -> None:
        def work() -> None:
            try:
                self.updates.stage(rel, lambda f: None)
            except Exception as exc:
                log.exception("update download failed")
                self.notify(f"Обновление не скачалось: {exc}")
                return
            if then_restart and not self.queue.busy():
                self.save()
                self.updates.apply_and_restart()
                self.page.run_task(self.page.window.close)
            else:
                self.updates.apply_on_next_start()
                self.notify(
                    "Обновление скачано и установится при следующем запуске"
                    + (" (сейчас идёт обработка видео)." if self.queue.busy() else ".")
                )

        self.page.run_thread(work)


def main(page: ft.Page) -> None:
    App(page).start()


def run() -> None:
    port = os.environ.get("AUTODUB_WEB_PORT")
    if port:  # headless web mode for automated screenshots and UI checks
        ft.run(main, view=None, port=int(port), assets_dir=str(ASSETS))
    else:
        ft.run(main, assets_dir=str(ASSETS))


if __name__ == "__main__":
    run()
