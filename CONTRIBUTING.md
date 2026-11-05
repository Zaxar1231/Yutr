# Как помочь проекту

Спасибо, что хотите помочь! Ниже — всё, что нужно, чтобы начать.

## Окружение

```bash
git clone https://github.com/Zaxar1231/Yutr && cd Yutr
uv sync                     # Python 3.12 и зависимости ядра
uv run pytest               # тесты
uv run autodub system       # что программа думает о вашем железе
uv run python -m autodub.app.main   # интерфейс
```

Тяжёлые зависимости (PyTorch, Demucs, WhisperX, XTTS, F5-TTS) живут в отдельном окружении-движке.
Создайте его так же, как это делает программа (`autodub/components/envs.py`), или укажите своё:
`AUTODUB_ENGINE_PYTHON=путь/к/python`.

## Правила

- **Коммиты** — [Conventional Commits](https://www.conventionalcommits.org/ru/): `feat:`, `fix:`, `docs:`,
  `refactor:`, `test:`, `ci:`. Из них собирается changelog релиза.
- **Перед PR:** `uv run ruff check src tests scripts`, `uv run ruff format src tests scripts`,
  `uv run mypy`, `uv run pytest`.
- **Тексты интерфейса и ошибок** — на русском, без технического жаргона и стектрейсов. Технические
  подробности — в лог.
- **Никаких значений под конкретный ПК.** Всё, что зависит от железа, — в `hardware/data/profiles.toml`.
- **Модели и движки** подключаются через реестр: новый этап или модель не должны требовать правок
  в остальном пайплайне.
- **Скриншоты** после изменений интерфейса: `uv run python scripts/screenshots.py`.

## Выпуск версии

1. Поднимите версию в `pyproject.toml` (единственное место, где она задаётся).
2. Перенесите «Unreleased» в CHANGELOG.md.
3. `git tag vX.Y.Z && git push --tags` — GitHub Actions соберёт установщик, архив обновления,
   `checksums.txt` и опубликует релиз. Теги с `-beta.N` уходят в бета-канал.
