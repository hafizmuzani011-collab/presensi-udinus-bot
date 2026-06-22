"""Custom alias commands. Map custom name → perintah bot yang dieksekusi."""
import json
import os

ALIAS_FILE = "alias.json"


def load_aliases() -> dict:
    if not os.path.exists(ALIAS_FILE):
        return {}
    try:
        with open(ALIAS_FILE) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_aliases(aliases: dict) -> None:
    with open(ALIAS_FILE, "w", encoding="utf-8") as f:
        json.dump(aliases, f, indent=2, ensure_ascii=False)


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
