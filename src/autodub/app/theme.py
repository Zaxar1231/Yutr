"""Material 3 theme with the Windows accent colour as the Material You seed."""

from __future__ import annotations

import sys

import flet as ft

DEFAULT_SEED = "#3F51B5"


def windows_accent() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\DWM") as k:
            abgr = int(winreg.QueryValueEx(k, "AccentColor")[0])
    except OSError:
        return None
    r, g, b = abgr & 0xFF, (abgr >> 8) & 0xFF, (abgr >> 16) & 0xFF
    return f"#{r:02X}{g:02X}{b:02X}"


def apply_theme(page: ft.Page, mode: str = "system", seed: str | None = None) -> None:
    color = seed or windows_accent() or DEFAULT_SEED
    page.theme = ft.Theme(color_scheme_seed=color, use_material3=True)
    page.dark_theme = ft.Theme(color_scheme_seed=color, use_material3=True)
    page.theme_mode = {"light": ft.ThemeMode.LIGHT, "dark": ft.ThemeMode.DARK}.get(mode, ft.ThemeMode.SYSTEM)
