"""Настройки: у каждого параметра — понятное описание, что он делает и на что влияет."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import flet as ft

from autodub.core.config import AppConfig
from autodub.core.secrets import get_secret, set_secret

TRANSLATORS = {
    "claude": "Claude API — живой перевод с учётом контекста (нужен ключ)",
    "madlad": "Офлайн — MADLAD-400, без интернета, перевод суше",
}
CLAUDE_MODELS = {
    "claude-opus-5-5": "Claude Opus 5.5 — лучшее качество",
    "claude-sonnet-5-5": "Claude Sonnet 5.5 — дешевле и быстрее",
}
TTS = {
    "auto": "Автоматически (по профилю)",
    "xtts_v2": "XTTS-v2 — стабильная, быстрая (некоммерческая лицензия)",
    "f5_tts_ru": "F5-TTS русская — естественнее интонации, медленнее (некоммерческая)",
}
BROWSERS = {
    "": "Не использовать",
    "chrome": "Chrome",
    "edge": "Edge",
    "firefox": "Firefox",
    "yandex": "Яндекс Браузер",
    "opera": "Opera",
    "brave": "Brave",
}
THEMES = {"system": "Как в системе", "light": "Светлая", "dark": "Тёмная"}


def setting(title: str, description: str, control: ft.Control) -> ft.Control:
    return ft.ResponsiveRow(
        [
            ft.Column(
                [
                    ft.Text(title, weight=ft.FontWeight.W_500),
                    ft.Text(description, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                ],
                spacing=2,
                col={"xs": 12, "md": 6},
            ),
            ft.Container(control, col={"xs": 12, "md": 6}, align=ft.Alignment.CENTER_LEFT),
        ],
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )


def section(title: str, icon: ft.IconData, rows: list[ft.Control]) -> ft.Control:
    return ft.Card(
        variant=ft.CardVariant.OUTLINED,
        content=ft.Container(
            padding=20,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Icon(icon, color=ft.Colors.PRIMARY),
                            ft.Text(title, theme_style=ft.TextThemeStyle.TITLE_MEDIUM),
                        ]
                    ),
                    *rows,
                ],
                spacing=16,
            ),
        ),
    )


def dropdown(
    value: str, options: dict[str, str], on_select: Callable[[str], None], width: int = 380
) -> ft.Dropdown:
    return ft.Dropdown(
        value=value,
        width=width,
        dense=True,
        options=[ft.DropdownOption(key=k, text=v) for k, v in options.items()],
        on_select=lambda e: on_select(e.control.value or ""),
    )


class SettingsScreen(ft.Column):
    def __init__(
        self,
        config: AppConfig,
        save: Callable[[], None],
        notify: Callable[[str], None],
        apply_theme: Callable[[str], None],
        output_dir: Callable[[], str],
    ):
        super().__init__(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.config, self.save, self.notify = config, save, notify
        self.apply_theme, self.output_dir = apply_theme, output_dir
        self.build_controls()

    def _set(self, obj: Any, field: str, value: Any) -> None:
        setattr(obj, field, value)
        self.save()

    def _secret_field(self, name: str, label: str) -> ft.Control:
        has = bool(get_secret(name))
        tf = ft.TextField(
            password=True,
            can_reveal_password=True,
            dense=True,
            width=380,
            hint_text="сохранён" if has else label,
        )

        def store(_e: Any) -> None:
            value = (tf.value or "").strip() or None
            try:
                set_secret(name, value)
            except Exception:
                self.notify("Не удалось сохранить: хранилище учётных данных недоступно.")
                return
            tf.value = ""
            tf.hint_text = "сохранён" if value else label
            self.notify("Сохранено в защищённом хранилище Windows." if value else "Удалено.")
            self.update()

        return ft.Row([tf, ft.IconButton(ft.Icons.SAVE, tooltip="Сохранить", on_click=store)])

    async def _pick_folder(self, _e: Any) -> None:
        path = await ft.FilePicker().get_directory_path(dialog_title="Папка для готовых видео")
        if path:
            self._set(self.config, "output_dir", path)
            self.build_controls()
            self.update()

    def build_controls(self) -> None:
        c, d, u = self.config, self.config.defaults, self.config.updates
        vol = ft.Slider(
            min=0,
            max=0.6,
            divisions=12,
            value=d.original_volume,
            width=300,
            label="{value}",
            on_change_end=lambda e: self._set(d, "original_volume", round(float(e.control.value), 2)),
        )
        self.controls = [
            ft.Text("Настройки", theme_style=ft.TextThemeStyle.HEADLINE_SMALL),
            section(
                "Перевод",
                ft.Icons.TRANSLATE,
                [
                    setting(
                        "Режим перевода",
                        "Claude переводит как живой человек: учитывает контекст всего видео, держит "
                        "термины и укладывает фразы в тайминг. Офлайн-режим работает без интернета, "
                        "но переводит дословнее и иногда длиннее, чем нужно.",
                        dropdown(d.translator, TRANSLATORS, lambda v: self._set(d, "translator", v)),
                    ),
                    setting(
                        "Модель Claude",
                        "Opus переводит точнее, Sonnet дешевле. Оплата — по вашему ключу API.",
                        dropdown(d.claude_model, CLAUDE_MODELS, lambda v: self._set(d, "claude_model", v)),
                    ),
                    setting(
                        "Ключ Claude API",
                        "Хранится в диспетчере учётных данных Windows, а не в файле настроек. "
                        "Получить ключ: console.anthropic.com.",
                        self._secret_field("anthropic_api_key", "sk-ant-…"),
                    ),
                ],
            ),
            section(
                "Голос и звук",
                ft.Icons.RECORD_VOICE_OVER,
                [
                    setting(
                        "Модель озвучки",
                        "Клонирует голос спикера по образцу из видео. Модели работают локально.",
                        dropdown(
                            d.tts_engine or "auto",
                            TTS,
                            lambda v: self._set(d, "tts_engine", None if v == "auto" else v),
                        ),
                    ),
                    setting(
                        "Режим",
                        "«Дубляж» полностью убирает оригинальный голос. «Закадровый» оставляет "
                        "его тихо под переводом, как в документальных фильмах.",
                        dropdown(
                            d.mode,
                            {"dub": "Дубляж", "voiceover": "Закадровый перевод"},
                            lambda v: self._set(d, "mode", v),
                        ),
                    ),
                    setting(
                        "Громкость оригинала",
                        "Только для закадрового режима: насколько слышен оригинальный голос под переводом.",
                        vol,
                    ),
                    setting(
                        "Разные голоса для разных людей",
                        "Определяет, кто говорит, и даёт каждому свой голос. Нужен бесплатный токен "
                        "Hugging Face с доступом к pyannote/speaker-diarization-community-1.",
                        ft.Switch(
                            value=d.diarize,
                            on_change=lambda e: self._set(d, "diarize", bool(e.control.value)),
                        ),
                    ),
                    setting(
                        "Токен Hugging Face",
                        "Нужен только для разделения по спикерам.",
                        self._secret_field("hf_token", "hf_…"),
                    ),
                ],
            ),
            section(
                "Скачивание и сохранение",
                ft.Icons.DOWNLOAD,
                [
                    setting(
                        "Качество по умолчанию",
                        "Чем выше качество, тем больше файл и дольше скачивание. "
                        "На скорость перевода не влияет.",
                        dropdown(
                            d.quality,
                            {"best": "Лучшее", "1080": "1080p", "720": "720p", "audio": "Только аудио"},
                            lambda v: self._set(d, "quality", v),
                            width=200,
                        ),
                    ),
                    setting(
                        "Cookies из браузера",
                        "Для закрытых видео и видео «только для взрослых»: yt-dlp возьмёт вход из браузера. "
                        "Браузер лучше закрыть перед скачиванием.",
                        dropdown(
                            d.cookies_from_browser,
                            BROWSERS,
                            lambda v: self._set(d, "cookies_from_browser", v),
                            width=240,
                        ),
                    ),
                    setting(
                        "Папка для готовых видео",
                        self.output_dir(),
                        ft.OutlinedButton(
                            content="Выбрать…", icon=ft.Icons.FOLDER, on_click=self._pick_folder
                        ),
                    ),
                ],
            ),
            section(
                "Обновления и внешний вид",
                ft.Icons.SYSTEM_UPDATE,
                [
                    setting(
                        "Канал обновлений",
                        "Бета получает новые функции раньше, но может быть менее стабильной.",
                        dropdown(
                            u.channel,
                            {"stable": "Стабильный", "beta": "Бета"},
                            lambda v: self._set(u, "channel", v),
                            width=240,
                        ),
                    ),
                    setting(
                        "Обновлять автоматически",
                        "Новая версия скачается в фоне и установится при следующем "
                        "запуске. Задачи в очереди не прерываются.",
                        ft.Switch(
                            value=u.auto_update,
                            on_change=lambda e: self._set(u, "auto_update", bool(e.control.value)),
                        ),
                    ),
                    setting(
                        "Тема",
                        "Цвет акцента берётся из Windows (Material You).",
                        dropdown(c.theme, THEMES, self._theme, width=240),
                    ),
                ],
            ),
        ]

    def _theme(self, value: str) -> None:
        self._set(self.config, "theme", value)
        self.apply_theme(value)
