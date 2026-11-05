"""Demo queue for screenshots: jobs in every interesting state."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path


def build(output: str | None, source: str | None) -> list[dict]:
    now = time.time()
    base = {
        "stage_count": 14,
        "notices": [],
        "eta": None,
        "message": "",
        "error": None,
        "output": None,
        "source": None,
    }
    return [
        base
        | {
            "id": "a1",
            "options": {"url": "https://www.youtube.com/watch?v=demo1", "quality": "1080"},
            "status": "running",
            "title": "How Rockets Work — Explained",
            "stage": "tts",
            "stage_title": "Озвучка",
            "stage_index": 10,
            "fraction": 0.62,
            "overall": 0.71,
            "eta": 184,
            "created": now,
        },
        base
        | {
            "id": "b2",
            "options": {"url": "https://www.youtube.com/watch?v=demo2", "quality": "best"},
            "status": "queued",
            "title": "https://www.youtube.com/watch?v=demo2",
            "stage_index": 0,
            "fraction": 0,
            "overall": 0,
            "created": now - 10,
        },
        base
        | {
            "id": "c3",
            "options": {"url": "https://archive.org/details/GETLAMP-Eilers", "quality": "720"},
            "status": "done",
            "title": "GET LAMP Michael Eilers Interview [RU]",
            "stage_index": 13,
            "fraction": 1,
            "overall": 1,
            "output": output,
            "source": source,
            "created": now - 600,
            "notices": [],
        },
        base
        | {
            "id": "d4",
            "options": {"url": "https://archive.org/details/nsm13b", "quality": "best"},
            "status": "already_russian",
            "title": "Новости с мнением. Выпуск #13",
            "stage_index": 2,
            "fraction": 1,
            "overall": 0.1,
            "created": now - 900,
        },
    ]


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.write_text(
        json.dumps(
            build(sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else None),
            ensure_ascii=False,
        ),
        "utf-8",
    )
