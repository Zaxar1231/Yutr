## Что сделано

<!-- Коротко: что меняется и зачем. Ссылка на issue: Closes #… -->

## Как проверено

- [ ] `uv run ruff check src tests scripts && uv run ruff format --check src tests scripts`
- [ ] `uv run mypy`
- [ ] `uv run pytest`
- [ ] Проверено вручную (опишите, на каком железе / профиле)

## Скриншоты

<!-- Если менялся интерфейс: обновите их через `uv run python scripts/screenshots.py` -->

## Чек-лист

- [ ] Тексты интерфейса и ошибок — на русском, понятным языком
- [ ] Нет захардкоженных значений под конкретный ПК
- [ ] Сообщения коммитов — в стиле Conventional Commits
- [ ] CHANGELOG.md обновлён (раздел «Unreleased»)
