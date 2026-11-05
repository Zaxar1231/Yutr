"""«Моя система»: detected hardware, chosen profile with a plain explanation, time estimate,
manual overrides and «Вернуть авто»."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import flet as ft

from autodub.core.text import count
from autodub.hardware.models import Accelerator, HardwareReport
from autodub.hardware.profiles import TIER_ORDER, ProfileSelection, Tier
from autodub.hardware.service import HardwareService
from autodub.hardware.settings_meta import SETTINGS, SettingMeta

log = logging.getLogger(__name__)

ACCEL_LABEL = {
    Accelerator.CUDA: "NVIDIA CUDA",
    Accelerator.ROCM: "AMD ROCm",
    Accelerator.DIRECTML: "DirectML + процессор",
    Accelerator.CPU: "Процессор",
}


def _gb(mb: int | None) -> str:
    if mb is None:
        return "—"
    if mb >= 1000 * 1024:
        return f"{mb / 1024 / 1024:.1f} ТБ".replace(".", ",")
    return f"{mb / 1024:.1f} ГБ".replace(".", ",")


def _pill(icon: ft.IconData, text: str) -> ft.Control:
    """Static label in MD3 assist-chip style (a real Chip without handlers looks disabled)."""
    return ft.Container(
        ft.Row(
            [ft.Icon(icon, size=18, color=ft.Colors.PRIMARY), ft.Text(text, size=13)],
            spacing=6,
            tight=True,
        ),
        bgcolor=ft.Colors.SURFACE,
        border_radius=8,
        padding=ft.Padding.symmetric(horizontal=12, vertical=6),
    )


def _yes(v: bool | None) -> str:
    return "неизвестно" if v is None else ("есть" if v else "нет")


def _kv(rows: list[tuple[str, str]]) -> ft.Column:
    return ft.Column(
        [
            ft.Row(
                [
                    ft.Text(k, color=ft.Colors.ON_SURFACE_VARIANT, size=13, expand=True),
                    ft.Text(v, size=13, weight=ft.FontWeight.W_500, selectable=True),
                ],
            )
            for k, v in rows
        ],
        spacing=4,
    )


def hw_card(icon: ft.IconData, title: str, rows: list[tuple[str, str]], subtitle: str = "") -> ft.Card:
    return ft.Card(
        variant=ft.CardVariant.OUTLINED,
        col={"xs": 12, "md": 6, "xl": 4},
        content=ft.Container(
            padding=16,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                ft.Icon(icon, color=ft.Colors.ON_SECONDARY_CONTAINER, size=22),
                                bgcolor=ft.Colors.SECONDARY_CONTAINER,
                                border_radius=12,
                                padding=8,
                            ),
                            ft.Column(
                                [
                                    ft.Text(title, weight=ft.FontWeight.W_600, size=15),
                                    ft.Text(
                                        subtitle,
                                        size=12,
                                        color=ft.Colors.ON_SURFACE_VARIANT,
                                        visible=bool(subtitle),
                                        max_lines=1,
                                        overflow=ft.TextOverflow.ELLIPSIS,
                                    ),
                                ],
                                spacing=0,
                                expand=True,
                            ),
                        ],
                        spacing=12,
                    ),
                    _kv(rows),
                ],
                spacing=12,
            ),
        ),
    )


def hardware_cards(report: HardwareReport) -> list[ft.Control]:
    cards: list[ft.Control] = []
    gpu = report.primary_gpu()
    if gpu:
        rows = [("Видеопамять", "общая с ОЗУ" if gpu.integrated else _gb(gpu.vram_total_mb))]
        if gpu.integrated:
            rows.append(("Тип", "встроенная, для нейросетей не подходит"))
        if gpu.vram_free_mb is not None:
            rows.append(("Свободно сейчас", _gb(gpu.vram_free_mb)))
        if gpu.driver_version:
            rows.append(("Драйвер", gpu.driver_version))
        if gpu.cuda_version:
            rows.append(("CUDA (драйвер)", gpu.cuda_version))
        others = [g.name for g in report.gpus if g is not gpu]
        if others:
            rows.append(("Ещё", ", ".join(others)))
        cards.append(hw_card(ft.Icons.DEVELOPER_BOARD, "Видеокарта", rows, gpu.name))
    else:
        cards.append(hw_card(ft.Icons.DEVELOPER_BOARD_OFF, "Видеокарта", [("Найдено", "нет")], ""))
    c = report.cpu
    cards.append(
        hw_card(
            ft.Icons.MEMORY,
            "Процессор",
            [
                ("Ядра / потоки", f"{c.physical_cores} / {c.logical_threads}"),
                ("AVX2", _yes(c.avx2)),
                ("AVX-512", _yes(c.avx512)),
            ],
            c.name,
        )
    )
    m = report.memory
    cards.append(
        hw_card(
            ft.Icons.SD_STORAGE,
            "Оперативная память",
            [("Всего", _gb(m.total_mb)), ("Свободно", _gb(m.available_mb))],
        )
    )
    disk_rows: list[tuple[str, str]] = []
    for d in report.disks:
        role = "Модели" if d.role == "models" else "Видео"
        kind = {"ssd": "SSD", "hdd": "HDD"}.get(d.kind, "диск")
        disk_rows.append((f"{role} ({kind})", f"свободно {_gb(d.free_mb)} из {_gb(d.total_mb)}"))
    cards.append(hw_card(ft.Icons.STORAGE, "Диски", disk_rows or [("Данные", "—")]))
    enc = report.encoders
    hw = ", ".join(n.upper() for n in enc.available()) or "нет"
    cards.append(
        hw_card(
            ft.Icons.VIDEO_SETTINGS,
            "Аппаратное кодирование",
            [("ffmpeg", "найден" if enc.ffmpeg_found else "не найден"), ("Энкодеры", hw)],
        )
    )
    p, n = report.power, report.network
    power = "от сети"
    if p.on_battery:
        power = "от батареи" + (f" ({p.percent:.0f}%)" if p.percent is not None else "")
    cards.append(
        hw_card(
            ft.Icons.BOLT,
            "Питание и сеть",
            [
                ("Питание", power if p.has_battery else "от сети (ПК)"),
                (
                    "Скорость интернета",
                    f"{n.mbit_per_s:.0f} Мбит/с" if n.mbit_per_s else "не измерена",
                ),
            ],
        )
    )
    return cards


class SystemScreen(ft.Column):
    """The whole screen. ``notify`` shows a snackbar in the app shell."""

    def __init__(self, service: HardwareService, notify: Callable[[str], None]):
        super().__init__(spacing=16, scroll=ft.ScrollMode.AUTO, expand=True)
        self.service = service
        self.notify = notify
        self.busy = ft.ProgressRing(width=20, height=20, stroke_width=2, visible=False)
        self.rebuild()

    # --- layout ---------------------------------------------------------------------------

    def rebuild(self) -> None:
        report = self.service.state.report
        header = ft.Row(
            [
                ft.Text("Моя система", theme_style=ft.TextThemeStyle.HEADLINE_SMALL, expand=True),
                self.busy,
                ft.OutlinedButton(
                    content="Вернуть авто",
                    icon=ft.Icons.RESTART_ALT,
                    on_click=self._reset,
                    tooltip="Убрать все ручные настройки и вернуть автоматический профиль",
                ),
                ft.FilledButton(
                    content="Пересканировать",
                    icon=ft.Icons.REFRESH,
                    on_click=self._rescan,
                ),
            ],
            spacing=8,
        )
        if report is None:
            self.controls = [
                header,
                ft.Text("Железо ещё не определено. Нажмите «Пересканировать»."),
            ]
            return
        sel = self.service.selection()
        self.controls = [
            header,
            self._profile_card(sel),
            *self._warnings(sel, report),
            ft.Text("Найденное железо", theme_style=ft.TextThemeStyle.TITLE_MEDIUM),
            ft.ResponsiveRow(hardware_cards(report), spacing=12, run_spacing=12),
            self._overrides(sel),
            ft.Text(
                f"Последнее сканирование: {report.scanned_at.replace('T', ' ')[:16]} UTC",
                size=12,
                color=ft.Colors.ON_SURFACE_VARIANT,
            ),
        ]

    def _profile_card(self, sel: ProfileSelection) -> ft.Control:
        lo, hi = self.service.estimate_10min()
        manual = sel.overridden or self.service.config.hardware.forced_tier
        chips = [_pill(ft.Icons.SPEED, ACCEL_LABEL[sel.accelerator])]
        if manual:
            chips.append(_pill(ft.Icons.TUNE, "Есть ручные настройки"))
        if sel.eco:
            chips.append(_pill(ft.Icons.ECO, "Экономный режим"))
        cal = self.service.state.calibration
        cal_text = (
            f"Уточнено калибровкой: скорость ×{cal.speed_factor}".replace(".", ",")
            if cal and not self.service.calibration_needed()
            else "Калибровка ещё не проводилась — оценка по характеристикам."
        )
        return ft.Card(
            variant=ft.CardVariant.FILLED,
            bgcolor=ft.Colors.PRIMARY_CONTAINER,
            content=ft.Container(
                padding=20,
                content=ft.Column(
                    [
                        ft.Text(
                            "Профиль производительности",
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                            size=13,
                        ),
                        ft.Text(
                            sel.tier.label,
                            theme_style=ft.TextThemeStyle.DISPLAY_SMALL,
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                            weight=ft.FontWeight.W_600,
                        ),
                        ft.Row(chips, wrap=True, spacing=8),
                        ft.Text(
                            "Почему этот профиль",
                            weight=ft.FontWeight.W_600,
                            color=ft.Colors.ON_PRIMARY_CONTAINER,
                        ),
                        ft.Column(
                            [ft.Text(f"• {r}", color=ft.Colors.ON_PRIMARY_CONTAINER) for r in sel.reasons],
                            spacing=2,
                        ),
                        ft.Divider(color=ft.Colors.with_opacity(0.2, ft.Colors.ON_PRIMARY_CONTAINER)),
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.TIMER_OUTLINED, color=ft.Colors.ON_PRIMARY_CONTAINER),
                                ft.Text(
                                    f"Видео на 10 минут обработается примерно за {lo}–{hi} {_minutes(hi)}",
                                    color=ft.Colors.ON_PRIMARY_CONTAINER,
                                    weight=ft.FontWeight.W_500,
                                    expand=True,
                                ),
                            ]
                        ),
                        ft.Text(cal_text, size=12, color=ft.Colors.ON_PRIMARY_CONTAINER),
                    ],
                    spacing=8,
                ),
            ),
        )

    def _warnings(self, sel: ProfileSelection, report: HardwareReport) -> list[ft.Control]:
        out: list[ft.Control] = []
        for w in [*sel.warnings, *report.warnings]:
            out.append(
                _banner(
                    ft.Icons.WARNING_AMBER_ROUNDED,
                    w,
                    ft.Colors.TERTIARY_CONTAINER,
                    ft.Colors.ON_TERTIARY_CONTAINER,
                )
            )
        if sel.suggest_eco:
            out.append(
                _banner(
                    ft.Icons.BATTERY_ALERT,
                    "Ноутбук работает от батареи. Включить экономный режим? "
                    "Обработка будет медленнее, зато батарея продержится дольше.",
                    ft.Colors.SECONDARY_CONTAINER,
                    ft.Colors.ON_SECONDARY_CONTAINER,
                    action=ft.TextButton(content="Включить", on_click=lambda _e: self._set_eco(True)),
                )
            )
        return out

    def _overrides(self, sel: ProfileSelection) -> ft.Control:
        hw = self.service.config.hardware
        tier_dd = ft.Dropdown(
            label="Профиль",
            value=hw.forced_tier or "auto",
            options=[ft.DropdownOption(key="auto", text="Автоматически")]
            + [ft.DropdownOption(key=t.value, text=t.label) for t in TIER_ORDER],
            on_select=self._on_tier,
            width=260,
        )
        eco = ft.Switch(
            label="Экономный режим",
            value=hw.eco_mode,
            on_change=lambda e: self._set_eco(bool(e.control.value)),
        )
        auto = self.service.automatic_selection().settings
        groups: dict[str, list[ft.Control]] = {}
        for key, meta in SETTINGS.items():
            value = getattr(sel.settings, key)
            groups.setdefault(meta.group, []).append(
                self._setting_row(key, meta, value, getattr(auto, key), key in sel.overridden)
            )
        sections: list[ft.Control] = []
        for group, rows in groups.items():
            sections.append(ft.Text(group, weight=ft.FontWeight.W_600, color=ft.Colors.PRIMARY))
            sections.extend(rows)
        return ft.Card(
            variant=ft.CardVariant.OUTLINED,
            content=ft.ExpansionTile(
                title="Ручная настройка",
                subtitle="Любой параметр профиля можно изменить. «Вернуть авто» отменит всё.",
                leading=ft.Icon(ft.Icons.TUNE),
                controls=[
                    ft.Container(
                        padding=ft.Padding.only(left=16, right=16, top=8, bottom=16),
                        content=ft.Column(
                            [ft.Row([tier_dd, eco], wrap=True, spacing=24), *sections],
                            spacing=10,
                        ),
                    )
                ],
            ),
        )

    def _setting_row(
        self, key: str, meta: SettingMeta, value: Any, auto_value: Any, manual: bool
    ) -> ft.Control:
        control: ft.Control
        if meta.kind == "choice":
            options = dict(meta.choices)
            if str(value) not in options:
                options[str(value)] = str(value)
            control = ft.Dropdown(
                value=str(value),
                options=[ft.DropdownOption(key=k, text=v) for k, v in options.items()],
                on_select=lambda e, k=key: self._set(k, e.control.value),
                width=300,
                dense=True,
            )
        elif meta.kind == "bool":
            control = ft.Switch(value=bool(value), on_change=lambda e, k=key: self._set(k, e.control.value))
        else:
            control = ft.TextField(
                value=str(value),
                width=120,
                dense=True,
                keyboard_type=ft.KeyboardType.NUMBER,
                on_submit=lambda e, k=key, m=meta: self._set_number(k, m, e.control.value),
                on_blur=lambda e, k=key, m=meta: self._set_number(k, m, e.control.value),
            )
        badge = ft.Container(
            ft.Text(
                "вручную" if manual else "авто",
                size=11,
                color=ft.Colors.ON_TERTIARY_CONTAINER if manual else ft.Colors.ON_SURFACE_VARIANT,
            ),
            bgcolor=ft.Colors.TERTIARY_CONTAINER if manual else ft.Colors.SURFACE_CONTAINER_HIGH,
            border_radius=8,
            padding=ft.Padding.symmetric(horizontal=8, vertical=2),
            tooltip=None if not manual else f"Автоматически было бы: {auto_value}",
        )
        return ft.ResponsiveRow(
            [
                ft.Column(
                    [
                        ft.Row([ft.Text(meta.label, weight=ft.FontWeight.W_500), badge], spacing=8),
                        ft.Text(meta.description, size=12, color=ft.Colors.ON_SURFACE_VARIANT),
                    ],
                    spacing=2,
                    col={"xs": 12, "md": 7},
                ),
                ft.Container(control, col={"xs": 12, "md": 5}, align=ft.Alignment.CENTER_LEFT),
            ],
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

    # --- actions --------------------------------------------------------------------------

    def _refresh(self) -> None:
        self.rebuild()
        self.update()

    def _set(self, key: str, value: Any) -> None:
        try:
            self.service.set_override(key, value)
        except ValueError:
            self.notify("Такое значение не подходит.")
        self._refresh()

    def _set_number(self, key: str, meta: SettingMeta, raw: str) -> None:
        try:
            num: float | int = float(raw.replace(",", ".")) if meta.kind == "float" else int(raw)
        except ValueError:
            self.notify("Введите число.")
            self._refresh()
            return
        current = getattr(self.service.selection().settings, key)
        if num == current:
            return
        if (meta.minimum is not None and num < meta.minimum) or (
            meta.maximum is not None and num > meta.maximum
        ):
            self.notify(f"Допустимо от {meta.minimum:g} до {meta.maximum:g}.")
            self._refresh()
            return
        self._set(key, num)

    def _on_tier(self, e: ft.Event[ft.Dropdown]) -> None:
        value = e.control.value
        self.service.set_forced_tier(None if value in (None, "auto") else Tier(value))
        self._refresh()

    def _set_eco(self, enabled: bool) -> None:
        self.service.set_eco(enabled)
        self._refresh()

    def _reset(self, _e: Any = None) -> None:
        self.service.reset_to_auto()
        self.notify("Все параметры снова подбираются автоматически.")
        self._refresh()

    def _rescan(self, _e: Any = None) -> None:
        self.busy.visible = True
        self.update()
        page = self.page
        assert page is not None

        def work() -> None:
            try:
                self.service.scan()
                self.notify("Железо определено заново.")
            except Exception:
                log.exception("rescan failed")
                self.notify("Не удалось пересканировать. Подробности записаны в журнал.")
            finally:
                self.busy.visible = False
                self._refresh()

        page.run_thread(work)


def _minutes(n: int) -> str:
    return count(n, "минуту", "минуты", "минут").split(" ", 1)[1]


def _banner(icon: ft.IconData, text: str, bg: str, fg: str, action: ft.Control | None = None) -> ft.Control:
    row: list[ft.Control] = [
        ft.Icon(icon, color=fg),
        ft.Text(text, color=fg, expand=True),
    ]
    if action:
        row.append(action)
    return ft.Container(
        ft.Row(row, spacing=12, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        bgcolor=bg,
        border_radius=16,
        padding=ft.Padding.symmetric(horizontal=16, vertical=12),
    )
