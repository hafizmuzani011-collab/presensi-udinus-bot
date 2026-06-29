"""Custom alias commands. Map custom name → perintah bot yang dieksekusi."""
import asyncio
import json
import os
import threading

from file_utils import atomic_write

ALIAS_FILE = "alias.json"
_aliases_lock = threading.Lock()


def load_aliases() -> dict:
    with _aliases_lock:
        if not os.path.exists(ALIAS_FILE):
            return {}
        try:
            with open(ALIAS_FILE, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}


def save_aliases(aliases: dict) -> None:
    with _aliases_lock:
        atomic_write(ALIAS_FILE, json.dumps(aliases, indent=2, ensure_ascii=False))


def resolve_alias(text: str) -> str | None:
    """Cek apakah text adalah alias. Kalau iya, return perintah asli."""
    aliases = load_aliases()
    text_lower = text.lower().strip()
    for alias, command in aliases.items():
        if text_lower == alias.lower() or text_lower == f"/{alias.lower()}":
            return command
    return None


def add_alias(name: str, command: str) -> None:
    aliases = load_aliases()
    aliases[name.strip().lower()] = command.strip()
    save_aliases(aliases)


def remove_alias(name: str) -> bool:
    aliases = load_aliases()
    if name.lower() in aliases:
        del aliases[name.lower()]
        save_aliases(aliases)
        return True
    return False


async def aresolve_alias(text: str) -> str | None:
    return await asyncio.to_thread(resolve_alias, text)


async def aadd_alias(name: str, command: str) -> None:
    await asyncio.to_thread(add_alias, name, command)


async def aremove_alias(name: str) -> bool:
    return await asyncio.to_thread(remove_alias, name)
