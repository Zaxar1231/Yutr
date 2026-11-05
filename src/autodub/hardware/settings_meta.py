"""Human descriptions of every profile setting, for the «Моя система» override editor."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Kind = Literal["choice", "int", "float", "bool"]


@dataclass(frozen=True)
class SettingMeta:
    label: str
    description: str
    kind: Kind
    group: str
    choices: dict[str, str] = field(default_factory=dict)  # value -> label
    minimum: float | None = None
    maximum: float | None = None


DEVICES = {"cuda": "Видеокарта", "cpu": "Процессор"}
WHISPER_MODELS = {
    "large-v3": "large-v3 — лучшая точность",
    "large-v3-turbo": "large-v3-turbo — почти та же точность, быстрее",
    "medium": "medium — средняя",
    "small": "small — быстрая, для слабых ПК",
}
COMPUTE_TYPES = {
    "float16": "float16 — точнее, больше памяти",
    "int8_float16": "int8_float16 — экономит видеопамять",
    "int8_float32": "int8_float32 — для видеокарт без тензорных ядер",
    "int8": "int8 — для процессора",
    "float32": "float32 — максимальная точность, медленно",
}

SETTINGS: dict[str, SettingMeta] = {
    "whisper_model": SettingMeta(
        "Модель распознавания речи",
        "Чем больше модель, тем точнее текст и тем больше нужно памяти и времени.",
        "choice",
        "Распознавание",
        WHISPER_MODELS,
    ),
    "whisper_compute_type": SettingMeta(
        "Точность вычислений Whisper",
        "Влияет на расход видеопамяти и скорость. На качество почти не влияет.",
        "choice",
        "Распознавание",
        COMPUTE_TYPES,
    ),
    "whisper_batch_size": SettingMeta(
        "Размер пакета Whisper",
        "Сколько кусков речи распознаётся одновременно. Больше — быстрее, но нужно больше памяти.",
        "int",
        "Распознавание",
        minimum=1,
        maximum=64,
    ),
    "whisper_device": SettingMeta(
        "Где работает Whisper",
        "Видеокарта во много раз быстрее процессора.",
        "choice",
        "Распознавание",
        DEVICES,
    ),
    "align_device": SettingMeta(
        "Где выравниваются слова",
        "Привязка каждого слова ко времени для точного тайминга.",
        "choice",
        "Распознавание",
        DEVICES,
    ),
    "demucs_model": SettingMeta(
        "Модель отделения голоса",
        "htdemucs_ft чище отделяет голос от музыки, но работает в 4 раза дольше.",
        "choice",
        "Разделение звука",
        {"htdemucs_ft": "htdemucs_ft — чище, медленнее", "htdemucs": "htdemucs — быстрее"},
    ),
    "demucs_segment": SettingMeta(
        "Длина сегмента Demucs, с",
        "Короткие сегменты экономят видеопамять. Длинные дают чуть более ровный звук.",
        "float",
        "Разделение звука",
        minimum=1,
        maximum=7.8,
    ),
    "demucs_shifts": SettingMeta(
        "Число проходов Demucs",
        "Каждый проход немного улучшает качество и добавляет время.",
        "int",
        "Разделение звука",
        minimum=0,
        maximum=5,
    ),
    "separation_engine": SettingMeta(
        "Движок разделения",
        "Demucs работает через PyTorch. MDX через ONNX подходит для видеокарт AMD и Intel.",
        "choice",
        "Разделение звука",
        {"demucs": "Demucs", "mdx_onnx": "MDX-Net (ONNX)"},
    ),
    "separation_device": SettingMeta(
        "Где разделяется звук",
        "DirectML — видеокарты AMD и Intel без PyTorch.",
        "choice",
        "Разделение звука",
        DEVICES | {"directml": "DirectML"},
    ),
    "tts_model": SettingMeta(
        "Модель озвучки",
        "Голосовая модель, которая говорит по-русски голосом спикера.",
        "choice",
        "Озвучка",
        {"xtts_v2": "XTTS-v2", "f5_tts_ru": "F5-TTS (русская)"},
    ),
    "tts_precision": SettingMeta(
        "Точность озвучки",
        "fp16 быстрее и экономит память на современных видеокартах.",
        "choice",
        "Озвучка",
        {"fp16": "fp16", "fp32": "fp32"},
    ),
    "tts_device": SettingMeta(
        "Где работает озвучка",
        "На процессоре озвучка в десятки раз медленнее.",
        "choice",
        "Озвучка",
        DEVICES,
    ),
    "keep_models_loaded": SettingMeta(
        "Держать модели в памяти",
        "Модели не выгружаются между видео в очереди. Быстрее, но занимает видеопамять.",
        "bool",
        "Память",
    ),
    "translate_offline_compute_type": SettingMeta(
        "Точность офлайн-перевода",
        "Используется только в офлайн-режиме перевода.",
        "choice",
        "Перевод",
        COMPUTE_TYPES,
    ),
    "translate_offline_device": SettingMeta(
        "Где работает офлайн-перевод",
        "Используется только в офлайн-режиме перевода.",
        "choice",
        "Перевод",
        DEVICES,
    ),
    "cpu_threads": SettingMeta(
        "Потоки процессора",
        "Сколько ядер занимает обработка. Меньше — компьютером удобнее пользоваться параллельно.",
        "int",
        "Система",
        minimum=1,
        maximum=256,
    ),
    "ffmpeg_encoder": SettingMeta(
        "Видеоэнкодер",
        "Видео обычно копируется без перекодирования. Энкодер нужен, только если формат "
        "видео несовместим с итоговым файлом.",
        "choice",
        "Система",
        {
            "h264_nvenc": "NVIDIA NVENC",
            "h264_qsv": "Intel Quick Sync",
            "h264_amf": "AMD AMF",
            "libx264": "Процессор (libx264)",
        },
    ),
    "ytdlp_fragments": SettingMeta(
        "Потоки скачивания",
        "Сколько частей видео качается одновременно. Подбирается по скорости интернета.",
        "int",
        "Скачивание",
        minimum=1,
        maximum=64,
    ),
    "aria2_connections": SettingMeta(
        "Соединения aria2c",
        "Число соединений на один файл при скачивании.",
        "int",
        "Скачивание",
        minimum=1,
        maximum=16,
    ),
}
