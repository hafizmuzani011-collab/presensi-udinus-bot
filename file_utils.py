"""Shared file I/O utilities — atomic writes, path helpers."""
import json
import os
import logging

logger = logging.getLogger(__name__)


def atomic_write(path: str, data_str: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(data_str)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def atomic_write_json(path: str, data: dict | list) -> None:
    atomic_write(path, json.dumps(data, indent=2, ensure_ascii=False))


def read_json(path: str) -> dict | list:
    if not os.path.exists(path):
        return {} if isinstance({}, type) else []
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"read_json corrupt {path}: {e}")
        return {}
