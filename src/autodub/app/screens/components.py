"""Менеджер компонентов: ffmpeg, yt-dlp, модели — статус, размер, скачивание, «Проверить и починить»."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from autodub.components.manager import ComponentManager, Status

log = logging.getLogger(__name__)
KIND = {"binary": "Программа", "data": "Данные", "model": "Модель", "env": "Библиотеки"}


class ComponentsScreen(ft.Column):
    def __init__(
        self,
        manager: ComponentManager,
        needed_models: Callable[[], list[str]],
        repair: Callable[[Callable[[float, str], None]], list[str]],
        notify: Callable[[str], None],
    ):
        super().__init__(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.manager, self.needed_models, self.repair_fn, self.notify = manager, needed_models, repair, notify
        self.progress = ft.ProgressBar(visible=False, border_radius=4)
        self.progress_text = ft.Text("", size=12, visible=False)
        self.busy = False
        self.statuses: list[Status] = []
        self.list_col = ft.Column(spacing=8)
        self.controls = [
            ft.Row(
                [
                    ft.Text("Компоненты", theme_style=ft.TextThemeStyle.HEADLINE_SMALL, expand=True),
                    ft.FilledButton(
                        content="Проверить и починить", icon=ft.Icons.HEALING, on_click=self._repair
                    ),
                ]
            ),
            ft.Text(
                "Всё, что нужно программе, скачивается автоматически. Здесь видно, что установлено, "
                "сколько занимает места, и можно докачать или обновить отдельный компонент.",
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
            self.progress,
            self.progress_text,
            self.list_col,
        ]
        self.refresh(deep=False)

    def refresh(self, *, deep: bool) -> None:
        try:
            self.statuses = self.manager.status(deep=deep)
        except Exception:
            log.exception("component status failed")
            self.statuses = []
        needed = set(self.needed_models())
        rows: list[ft.Control] = []
        for st in self.statuses:
            if st.kind == "model" and st.key not in needed and not st.installed:
                continue  # only the models the current profile needs
            size = (
                f"{st.size_mb / 1024:.1f} ГБ"
                if st.size_mb >= 1024
                else f"{st.size_mb:.0f} МБ"
                if st.size_mb
                else ""
            )
            badge = ft.Container(
                ft.Text(
                    "установлен" if st.installed else "не установлен",
                    size=11,
                    color=ft.Colors.ON_PRIMARY_CONTAINER if st.installed else ft.Colors.ON_ERROR_CONTAINER,
                ),
                bgcolor=ft.Colors.PRIMARY_CONTAINER if st.installed else ft.Colors.ERROR_CONTAINER,
                border_radius=8,
                padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            )
            action = ft.TextButton(
                content="Скачать" if not st.installed else "Переустановить",
                icon=ft.Icons.DOWNLOAD,
                on_click=lambda _e, s=st: self._install(s),
                disabled=st.kind == "env",
            )
            rows.append(
                ft.Card(
                    variant=ft.CardVariant.OUTLINED,
                    content=ft.ListTile(
                        leading=ft.Icon(
                            {
                                "model": ft.Icons.PSYCHOLOGY,
                                "binary": ft.Icons.TERMINAL,
                                "data": ft.Icons.DATASET,
                                "env": ft.Icons.MEMORY,
                            }[st.kind]
                        ),
                        title=ft.Row([ft.Text(st.title, weight=ft.FontWeight.W_500), badge], spacing=8),
                        subtitle=ft.Text(
                            f"{KIND[st.kind]} · {st.license}{' · ' + size if size else ''}"
                            + (" · только некоммерческое использование" if st.noncommercial else ""),
                            size=12,
                        ),
                        trailing=action,
                    ),
                )
            )
        self.list_col.controls = rows

    def _set_progress(self, frac: float, text: str) -> None:
        self.progress.value = frac
        self.progress_text.value = text
        if self.page:
            self.update()

    def _run(self, work: Callable[[], Any], done: str) -> None:
        if self.busy or self.page is None:
            return
        self.busy = True
        self.progress.visible = self.progress_text.visible = True
        self.update()

        def job() -> None:
            try:
                work()
                self.notify(done)
            except Exception as exc:
                log.exception("component action failed")
                self.notify(f"Не получилось: {exc}. Проверьте интернет и попробуйте ещё раз.")
            finally:
                self.busy = False
                self.progress.visible = self.progress_text.visible = False
                self.refresh(deep=False)
                if self.page:
                    self.update()

        self.page.run_thread(job)

    def _install(self, st: Status) -> None:
        if st.kind == "model":
            self._run(lambda: self.manager.install_model(st.key, self._set_progress), f"{st.title}: готово.")
        else:
            self._run(lambda: self.manager.install_binary(st.key, self._set_progress), f"{st.title}: готово.")

    def _repair(self, _e: Any) -> None:
        def work() -> None:
            fixed = self.repair_fn(self._set_progress)
            self.notify("Всё в порядке." if not fixed else f"Исправлено: {', '.join(fixed)}.")

        self._run(work, "Проверка завершена.")
