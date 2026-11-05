# Метаданные репозитория

## Описание

> Автодубляж видео на русский: скачивает по ссылке, переводит и озвучивает голосом оригинального
> спикера. Локально, с автонастройкой под железо. Whisper · Demucs · XTTS/F5-TTS · Claude · Material 3

## Команды `gh`

```bash
gh repo edit Zaxar1231/Yutr \
  --description "Автодубляж видео на русский голосом оригинального спикера: yt-dlp, Whisper, Demucs, XTTS/F5-TTS, Claude. Локально, Material Design 3" \
  --homepage "https://github.com/Zaxar1231/Yutr/releases/latest" \
  --add-topic yt-dlp --add-topic dubbing --add-topic voice-cloning --add-topic whisper \
  --add-topic tts --add-topic material-design-3 --add-topic flet --add-topic speech-translation \
  --add-topic xtts --add-topic f5-tts --add-topic demucs --add-topic whisperx --add-topic russian \
  --add-topic windows --add-topic python --add-topic ai-dubbing --add-topic claude

# Social preview загружается вручную: Settings → General → Social preview → docs/assets/social-preview.png
```

## Labels

```bash
gh label create bug --color d73a4a --description "Что-то работает не так" --force
gh label create enhancement --color a2eeef --description "Новая функция или улучшение" --force
gh label create triage --color fbca04 --description "Нужно разобраться" --force
gh label create hardware --color 5319e7 --description "Определение железа и профили" --force
gh label create pipeline --color 0e8a16 --description "Скачивание, распознавание, перевод, озвучка" --force
gh label create ui --color 1d76db --description "Интерфейс" --force
gh label create installer --color c5def5 --description "Установщик, обновления" --force
gh label create models --color f9d0c4 --description "Модели и их лицензии" --force
gh label create good-first-issue --color 7057ff --description "Хорошо для первого вклада" --force
gh label create docs --color 0075ca --description "Документация" --force
```
