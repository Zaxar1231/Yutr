"""Главный экран: ссылка, «Перевести» и очередь задач с прогрессом по этапам."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import flet as ft

from autodub.app.queue import Job, JobQueue
from autodub.core.config import AppConfig
from autodub.pipeline.context import JobOptions
from autodub.pipeline.models import MixMode, Quality

QUALITY = {"best": "Лучшее", "1080": "1080p", "720": "720p", "audio": "Только аудио"}
STATUS = {
    "queued": ("В очереди", ft.Icons.SCHEDULE),
    "running": ("Обработка", ft.Icons.AUTORENEW),
    "paused": ("Пауза", ft.Icons.PAUSE_CIRCLE_OUTLINE),
    "done": ("Готово", ft.Icons.CHECK_CIRCLE),
    "error": ("Ошибка", ft.Icons.ERROR_OUTLINE),
    "cancelled": ("Отменено", ft.Icons.CANCEL_OUTLINED),
    "already_russian": ("Уже на русском", ft.Icons.TRANSLATE),
}


def fmt_eta(seconds: float | None) -> str:
    if seconds is None:
        return "оцениваю время…"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"осталось ~{h} ч {m:02d} мин"
    return f"осталось ~{m} мин {s:02d} с" if m else f"осталось ~{s} с"


class JobCard(ft.Card):
    def __init__(self, job: Job, queue: JobQueue, open_result: Callable[[Job], None]):
        super().__init__(variant=ft.CardVariant.OUTLINED)
        self.job, self.queue, self.open_result = job, queue, open_result
        self.refresh()

    def refresh(self) -> None:
        j = self.job
        label, icon = STATUS.get(j.status, (j.status, ft.Icons.HELP_OUTLINE))
        running = j.status in ("running", "paused")
        actions: list[ft.Control] = []
        if j.status == "running":
            actions.append(
                ft.IconButton(ft.Icons.PAUSE, tooltip="Пауза", on_click=lambda _e: self.queue.pause(j))
            )
        if j.status == "paused":
            actions.append(
                ft.IconButton(
                    ft.Icons.PLAY_ARROW, tooltip="Продолжить", on_click=lambda _e: self.queue.resume(j)
                )
            )
        if j.status in ("error", "cancelled"):
            actions.append(
                ft.TextButton(
                    content="Продолжить", icon=ft.Icons.REPLAY, on_click=lambda _e: self.queue.resume(j)
                )
            )
        if j.status in ("running", "paused", "queued"):
            actions.append(
                ft.IconButton(ft.Icons.CLOSE, tooltip="Отменить", on_click=lambda _e: self.queue.cancel(j))
            )
        if j.status == "done":
            actions.append(
                ft.FilledButton(
                    content="Открыть", icon=ft.Icons.PLAY_CIRCLE, on_click=lambda _e: self.open_result(j)
                )
            )
        details: list[ft.Control] = []
        if running or j.status == "queued":
            details += [
                ft.Row(
                    [
                        ft.Text(
                            f"Этап {j.stage_index + 1} из {j.stage_count}: {j.stage_title or 'ожидание'}",
                            size=13,
                            expand=True,
                        ),
                        ft.Text(
                            fmt_eta(j.eta) if running else "", size=12, color=ft.Colors.ON_SURFACE_VARIANT
                        ),
                    ]
                ),
                ft.ProgressBar(value=j.fraction if running else None, border_radius=4),
                ft.ProgressBar(
                    value=j.overall,
                    border_radius=4,
                    color=ft.Colors.TERTIARY,
                    bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
                    bar_height=3,
                ),
            ]
        if j.status == "already_russian":
            details.append(ft.Text("Видео уже на русском — перевод не нужен.", size=13))
        if j.error:
            details.append(ft.Text(j.error, size=13, color=ft.Colors.ERROR))
        for n in j.notices[-2:]:
            details.append(ft.Text(f"ℹ {n}", size=12, color=ft.Colors.ON_SURFACE_VARIANT))
        self.content = ft.Container(
            padding=16,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(icon, color=ft.Colors.PRIMARY),
                            ft.Column(
                                [
                                    ft.Text(
                                        j.title or j.url,
                                        weight=ft.FontWeight.W_600,
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                    ft.Text(
                                        f"{label} · {QUALITY.get(j.options.get('quality', ''), '')}",
                                        size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                    ),
                                ],
                                spacing=0,
                                expand=True,
                            ),
                            *actions,
                        ],
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    *details,
                ],
                spacing=8,
            ),
        )


class HomeScreen(ft.Column):
    def __init__(
        self,
        config: AppConfig,
        queue: JobQueue,
        output_dir: Callable[[], Path],
        open_result: Callable[[Job], None],
        notify: Callable[[str], None],
        disk_check: Callable[[], str | None],
    ):
        super().__init__(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.config, self.queue, self.output_dir = config, queue, output_dir
        self.open_result, self.notify, self.disk_check = open_result, notify, disk_check
        self.cards: dict[str, JobCard] = {}
        self.url = ft.TextField(
            hint_text="Вставьте ссылку на видео (YouTube и ещё 1000+ сайтов)",
            prefix_icon=ft.Icons.LINK,
            expand=True,
            on_submit=self._submit,
            border_radius=28,
            filled=True,
        )
        self.quality = ft.Dropdown(
            value=config.defaults.quality,
            width=170,
            dense=True,
            label="Качество",
            options=[ft.DropdownOption(key=k, text=v) for k, v in QUALITY.items()],
        )
        self.mode = ft.SegmentedButton(
            selected=[config.defaults.mode],
            segments=[
                ft.Segment(value="dub", label=ft.Text("Дубляж"), icon=ft.Icon(ft.Icons.RECORD_VOICE_OVER)),
                ft.Segment(
                    value="voiceover", label=ft.Text("Закадровый"), icon=ft.Icon(ft.Icons.SPATIAL_AUDIO)
                ),
            ],
        )
        self.queue_col = ft.Column(spacing=10)
        self.empty = ft.Container(
            ft.Column(
                [
                    ft.Icon(ft.Icons.MOVIE_FILTER_OUTLINED, size=56, color=ft.Colors.OUTLINE),
                    ft.Text("Очередь пуста", theme_style=ft.TextThemeStyle.TITLE_MEDIUM),
                    ft.Text(
                        "Вставьте ссылку и нажмите «Перевести». Можно добавить несколько видео.",
                        color=ft.Colors.ON_SURFACE_VARIANT,
                        text_align=ft.TextAlign.CENTER,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=6,
            ),
            padding=40,
            alignment=ft.Alignment.CENTER,
        )
        self.controls = [
            ft.Text("Автодубляж", theme_style=ft.TextThemeStyle.HEADLINE_SMALL),
            ft.Card(
                variant=ft.CardVariant.FILLED,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                        [
                            ft.Row(
                                [
                                    self.url,
                                    ft.IconButton(
                                        ft.Icons.CONTENT_PASTE,
                                        tooltip="Вставить из буфера",
                                        on_click=self._paste,
                                    ),
                                ]
                            ),
                            ft.Row(
                                [
                                    self.quality,
                                    self.mode,
                                    ft.Container(expand=True),
                                    ft.FilledButton(
                                        content="Перевести",
                                        icon=ft.Icons.TRANSLATE,
                                        height=48,
                                        on_click=self._submit,
                                    ),
                                ],
                                spacing=12,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            ),
                        ],
                        spacing=12,
                    ),
                ),
            ),
            ft.Text("Очередь", theme_style=ft.TextThemeStyle.TITLE_MEDIUM),
            self.queue_col,
        ]
        self.render()

    def render(self) -> None:
        jobs = sorted(self.queue.jobs, key=lambda j: -j.created)
        self.queue_col.controls = [self._card(j) for j in jobs] or [self.empty]

    def _card(self, job: Job) -> JobCard:
        card = self.cards.get(job.id)
        if card is None:
            card = self.cards[job.id] = JobCard(job, self.queue, self.open_result)
        else:
            card.refresh()
        return card

    def job_changed(self, job: Job) -> None:
        self._card(job)
        self.render()
        if self.page:
            self.update()

    async def _paste(self, _e: Any) -> None:
        text = await ft.Clipboard().get()
        if text:
            self.url.value = text.strip()
            self.url.update()

    def _submit(self, _e: Any) -> None:
        url = (self.url.value or "").strip()
        if not url:
            self.notify("Вставьте ссылку на видео.")
            return
        if not (url.startswith(("http://", "https://")) or Path(url).exists()):
            self.notify("Это не похоже на ссылку. Скопируйте адрес видео из браузера.")
            return
        warning = self.disk_check()
        if warning:
            self.notify(warning)
            return
        d = self.config.defaults
        opts = JobOptions(
            url=url,
            quality=Quality(self.quality.value or "best"),
            mode=MixMode(next(iter(self.mode.selected or ["dub"]))),
            original_volume=d.original_volume,
            translator=d.translator,
            claude_model=d.claude_model,
            tts_engine=d.tts_engine,
            diarize=d.diarize,
            cookies_from_browser=d.cookies_from_browser or None,
            output_dir=str(self.output_dir()),
        )
        self.queue.add(opts)
        self.url.value = ""
        self.notify("Видео добавлено в очередь.")
        self.render()
        self.update()
