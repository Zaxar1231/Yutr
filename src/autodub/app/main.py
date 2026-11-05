"""Application shell: navigation rail, theme, startup hardware check."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import flet as ft

from autodub import __version__
from autodub.app.screens.system import SystemScreen
from autodub.app.theme import apply_theme
from autodub.core.config import load_config
from autodub.core.logs import setup_logging
from autodub.core.paths import resolve_paths
from autodub.hardware.models import HardwareReport
from autodub.hardware.service import HardwareService

log = logging.getLogger(__name__)

DESTINATIONS = [
    (ft.Icons.HOME_OUTLINED, ft.Icons.HOME, "Главная"),
    (ft.Icons.MEMORY_OUTLINED, ft.Icons.MEMORY, "Моя система"),
    (ft.Icons.SETTINGS_OUTLINED, ft.Icons.SETTINGS, "Настройки"),
    (ft.Icons.EXTENSION_OUTLINED, ft.Icons.EXTENSION, "Компоненты"),
    (ft.Icons.INFO_OUTLINE, ft.Icons.INFO, "О программе"),
]


def build_service() -> HardwareService:
    paths = resolve_paths().ensure()
    setup_logging(paths.logs)
    config = load_config(paths.config_file)
    demo = os.environ.get("AUTODUB_DEMO_HARDWARE")
    if demo:  # used by scripts/screenshots.py to show a fixed machine
        report = HardwareReport.model_validate_json(Path(demo).read_text("utf-8"))
        return HardwareService(
            paths=paths,
            config=config,
            scan_fn=lambda **_: report.model_copy(deep=True),
            fingerprint_fn=report.fingerprint,
        )
    return HardwareService(paths=paths, config=config)


def main(page: ft.Page) -> None:
    service = build_service()
    page.title = f"Автодубляж {__version__}"
    apply_theme(page, service.config.theme)
    page.padding = 0

    def notify(text: str) -> None:
        page.show_dialog(ft.SnackBar(ft.Text(text)))

    system = SystemScreen(service, notify)
    placeholder = ft.Container(
        ft.Text("Этот экран появится на следующих этапах.", color=ft.Colors.ON_SURFACE_VARIANT),
        alignment=ft.Alignment.CENTER,
        expand=True,
    )
    body = ft.Container(content=system, expand=True, padding=24)

    def on_nav(e: ft.Event[ft.NavigationRail]) -> None:
        body.content = system if e.control.selected_index == 1 else placeholder
        page.update()

    rail = ft.NavigationRail(
        selected_index=1,
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=88,
        destinations=[
            ft.NavigationRailDestination(icon=i, selected_icon=si, label=label)
            for i, si, label in DESTINATIONS
        ],
        on_change=on_nav,
    )
    page.add(ft.Row([rail, ft.VerticalDivider(width=1), body], expand=True, spacing=0))

    reason = service.rescan_reason()
    if reason:
        log.info("hardware scan needed: %s", reason)
        if reason == "hardware_changed":
            notify("Обнаружено новое железо или драйвер — подбираю настройки заново.")
        system._rescan()


def run() -> None:
    port = os.environ.get("AUTODUB_WEB_PORT")
    if port:  # headless web mode for automated screenshots and UI checks
        ft.run(main, view=None, port=int(port))
    else:
        ft.run(main)


if __name__ == "__main__":
    run()
