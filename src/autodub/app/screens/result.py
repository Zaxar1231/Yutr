"""Экран результата: плеер с переключением «перевод / оригинал» и «Открыть папку».

flet-video has no audio-track selector, so the two versions are two playlist items:
switching jumps to the other item and seeks to the same position.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import flet as ft

from autodub.app.queue import Job


def open_folder(path: Path) -> None:
    if sys.platform == "win32":
        subprocess.Popen(["explorer", "/select,", str(path)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path.parent)])


class ResultScreen(ft.Column):
    def __init__(self, back: Callable[[], None]):
        super().__init__(spacing=16, expand=True, scroll=ft.ScrollMode.AUTO)
        self.back = back
        self.job: Job | None = None
        self.track = "ru"
        self.player: Any = None

    def show(self, job: Job) -> None:
        self.job = job
        self.track = "ru"
        self.controls = self._build()

    def _player(self, job: Job) -> ft.Control:
        try:
            import flet_video as fv
        except ImportError:  # pragma: no cover
            return ft.Text("Плеер недоступен.")
        media = [fv.VideoMedia(resource=str(job.output))]
        if job.source and Path(job.source).exists():
            media.append(fv.VideoMedia(resource=str(job.source)))
        self.player = fv.Video(
            playlist=media, autoplay=False, aspect_ratio=16 / 9, expand=True, fill_color=ft.Colors.BLACK
        )
        return ft.Container(
            self.player, border_radius=16, clip_behavior=ft.ClipBehavior.ANTI_ALIAS, bgcolor=ft.Colors.BLACK
        )

    async def _switch(self, e: ft.Event[ft.SegmentedButton]) -> None:
        new = next(iter(e.control.selected or ["ru"]))
        if new == self.track or self.player is None:
            return
        self.track = new
        pos = await self.player.get_current_position()
        await self.player.jump_to(0 if new == "ru" else 1)
        await self.player.seek(pos)
        await self.player.play()

    def _build(self) -> list[ft.Control]:
        job = self.job
        assert job is not None and job.output
        out = Path(job.output)
        has_source = bool(job.source and Path(job.source).exists())
        subs = sorted(out.parent.glob(f"{out.stem}*.srt"))
        return [
            ft.Row(
                [
                    ft.IconButton(ft.Icons.ARROW_BACK, tooltip="Назад", on_click=lambda _e: self.back()),
                    ft.Text(
                        out.stem,
                        theme_style=ft.TextThemeStyle.HEADLINE_SMALL,
                        expand=True,
                        max_lines=1,
                        overflow=ft.TextOverflow.ELLIPSIS,
                    ),
                ]
            ),
            self._player(job),
            ft.Row(
                [
                    ft.SegmentedButton(
                        selected=["ru"],
                        segments=[
                            ft.Segment(
                                value="ru", label=ft.Text("Перевод"), icon=ft.Icon(ft.Icons.TRANSLATE)
                            ),
                            ft.Segment(
                                value="orig",
                                label=ft.Text("Оригинал"),
                                icon=ft.Icon(ft.Icons.LANGUAGE),
                                disabled=not has_source,
                            ),
                        ],
                        on_change=self._switch,
                    ),
                    ft.Container(expand=True),
                    ft.OutlinedButton(
                        content="Открыть папку",
                        icon=ft.Icons.FOLDER_OPEN,
                        on_click=lambda _e: open_folder(out),
                    ),
                ]
            ),
            ft.Card(
                variant=ft.CardVariant.OUTLINED,
                content=ft.Container(
                    padding=16,
                    content=ft.Column(
                        [
                            ft.Text("Файлы", weight=ft.FontWeight.W_600),
                            ft.Text(f"Видео: {out.name}", selectable=True),
                            *[ft.Text(f"Субтитры: {s.name}", selectable=True) for s in subs],
                            ft.Text(
                                "В видео две дорожки: русская (по умолчанию) и оригинальная. "
                                "Переключить их можно в любом плеере, например VLC.",
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                            *[ft.Text(f"ℹ {n}", size=12) for n in job.notices],
                        ],
                        spacing=6,
                    ),
                ),
            ),
            ft.Text(os.fspath(out.parent), size=12, color=ft.Colors.ON_SURFACE_VARIANT),
        ]
