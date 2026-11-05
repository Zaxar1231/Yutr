# Демо «до / после»

| Файл | Что это |
|---|---|
| [before.mp4](before.mp4) | 22 секунды оригинала |
| [after.mp4](after.mp4) | тот же фрагмент после «Автодубляжа» |

**Источник:** «GET LAMP: Michael Eilers Interview», Jason Scott, [archive.org/details/GETLAMP-Eilers](https://archive.org/details/GETLAMP-Eilers),
лицензия [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). Дубляж — производная работа под той же лицензией CC BY-SA 3.0.

**Как сделано:** профиль «Только процессор» (облачная машина без видеокарты, 4 ядра), Whisper small,
офлайн-перевод MADLAD-400, озвучка XTTS-v2 (лицензия CPML — некоммерческая), режим «Дубляж».
Команда: `autodub dub https://archive.org/details/GETLAMP-Eilers --section 120-210 --translator madlad --tts xtts_v2`.

Это честный худший случай: офлайн-перевод длиннее оригинала и не умеет сокращать фразы, поэтому
часть фраз ускорена до 1,2× и подрезана по паузам. В основном режиме (Claude) переводчик укладывает
фразы в тайминг сам.
