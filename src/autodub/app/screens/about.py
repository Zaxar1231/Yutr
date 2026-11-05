"""«О программе»: версия, ссылки, проверка обновлений, «Сообщить об ошибке»."""

from __future__ import annotations

import json
import urllib.parse
from collections.abc import Callable
from typing import Any

import flet as ft

from autodub import __version__

LICENSES = [
    ("yt-dlp", "Unlicense"),
    ("FFmpeg (сборка BtbN)", "GPL-3.0"),
    ("aria2", "GPL-2.0"),
    ("faster-whisper / CTranslate2", "MIT"),
    ("WhisperX", "BSD-2-Clause"),
    ("Demucs", "MIT"),
    ("pyannote.audio", "MIT; модель community-1 — CC-BY-4.0"),
    ("coqui-tts", "MPL-2.0"),
    ("XTTS-v2 (веса)", "CPML — некоммерческая"),
    ("F5-TTS", "MIT; русские веса — CC-BY-NC-4.0"),
    ("MADLAD-400", "Apache-2.0"),
    ("Flet", "Apache-2.0"),
    ("PyTorch", "BSD-3-Clause"),
]


def issue_url(repo: str, summary: dict[str, Any]) -> str:
    body = (
        "### Что случилось\n\n<!-- опишите, что вы делали и что пошло не так -->\n\n"
        "### Моя система\n\n```json\n" + json.dumps(summary, ensure_ascii=False, indent=1) + "\n```\n"
    )
    q = urllib.parse.urlencode(
        {"template": "bug_report.yml", "title": "[Ошибка] ", "system": body, "labels": "bug"}
    )
    return f"https://github.com/{repo}/issues/new?{q}"


class AboutScreen(ft.Column):
    def __init__(
        self,
        repo: str,
        check_updates: Callable[[], None],
        bug_summary: Callable[[], dict[str, Any]],
        open_url: Callable[[str], None],
    ):
        super().__init__(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.controls = [
            ft.Text("О программе", theme_style=ft.TextThemeStyle.HEADLINE_SMALL),
            ft.Card(
                variant=ft.CardVariant.FILLED,
                content=ft.Container(
                    padding=24,
                    content=ft.Row(
                        [
                            ft.Image(src="icon.png", width=72, height=72),
                            ft.Column(
                                [
                                    ft.Text("Автодубляж", theme_style=ft.TextThemeStyle.HEADLINE_MEDIUM),
                                    ft.Text(f"Версия {__version__}", color=ft.Colors.ON_SURFACE_VARIANT),
                                    ft.Text("Локальный дубляж видео на русский с клонированием голоса."),
                                ],
                                spacing=4,
                                expand=True,
                            ),
                        ],
                        spacing=20,
                    ),
                ),
            ),
            ft.Row(
                [
                    ft.FilledButton(
                        content="Проверить обновления",
                        icon=ft.Icons.SYSTEM_UPDATE,
                        on_click=lambda _e: check_updates(),
                    ),
                    ft.OutlinedButton(
                        content="GitHub",
                        icon=ft.Icons.CODE,
                        on_click=lambda _e: open_url(f"https://github.com/{repo}"),
                    ),
                    ft.OutlinedButton(
                        content="Сообщить об ошибке",
                        icon=ft.Icons.BUG_REPORT,
                        tooltip="Откроет форму на GitHub с версией, профилем и железом — без личных данных",
                        on_click=lambda _e: open_url(issue_url(repo, bug_summary())),
                    ),
                ],
                wrap=True,
            ),
            ft.Card(
                variant=ft.CardVariant.OUTLINED,
                content=ft.Container(
                    padding=20,
                    content=ft.Column(
                        [
                            ft.Text("Используемые проекты и лицензии", weight=ft.FontWeight.W_600),
                            *[
                                ft.Row(
                                    [
                                        ft.Text(n, expand=True),
                                        ft.Text(lic, color=ft.Colors.ON_SURFACE_VARIANT),
                                    ]
                                )
                                for n, lic in LICENSES
                            ],
                            ft.Text(
                                "Клонирование голоса — только для личного использования и с уважением "
                                "к правам "
                                "авторов. Не выдавайте синтезированную речь за слова реального человека.",
                                size=12,
                                color=ft.Colors.ON_SURFACE_VARIANT,
                            ),
                        ],
                        spacing=6,
                    ),
                ),
            ),
        ]
