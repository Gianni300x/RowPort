import argparse
import json
import os
from pathlib import Path

from .csv_io import csv_delimiter, csv_encoding


def database_url(path=Path(".env")):
    """Read the connection from the environment or the current directory's .env."""
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    if not path.exists():
        return None
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() == "DATABASE_URL":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value or None
    return None


CONFIG_FIELDS = {
    "table": str, "schema": str, "delimiter": str, "encoding": str,
    "map": list, "rejects": str, "skip_duplicates": bool, "empty_as_null": bool, "postgres_csv": bool,
}


def apply_config(parser, argv, fields):
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--config", type=Path)
    known, _ = pre.parse_known_args(argv)
    if not known.config:
        return
    try:
        config = json.loads(known.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("configuration must be a JSON object")
        unknown = set(config) - set(CONFIG_FIELDS)
        if unknown:
            raise ValueError("unknown configuration keys: " + ", ".join(sorted(unknown)))
        for key, value in config.items():
            if type(value) is not CONFIG_FIELDS[key]:
                raise ValueError(f"{key} must be {CONFIG_FIELDS[key].__name__}")
            if key == "map" and not all(isinstance(item, str) for item in value):
                raise ValueError("map must contain strings")
            if key in ("table", "schema", "rejects") and not value.strip():
                raise ValueError(f"{key} cannot be empty")
        if "delimiter" in config:
            csv_delimiter(config["delimiter"])
        if "encoding" in config:
            csv_encoding(config["encoding"])
        defaults = {key: value for key, value in config.items() if key in fields}
        if "rejects" in defaults:
            defaults["rejects"] = Path(defaults["rejects"])
        if any(arg == "--map" or arg.startswith("--map=") for arg in argv):
            defaults.pop("map", None)
        parser.set_defaults(**defaults)
    except (OSError, UnicodeError, ValueError, argparse.ArgumentTypeError) as exc:
        parser.error(f"Invalid configuration: {exc}")


