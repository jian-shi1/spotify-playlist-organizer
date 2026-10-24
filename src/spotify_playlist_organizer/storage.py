import json
import os
import tempfile
from pathlib import Path


class OrganizerError(Exception):
    """An actionable error safe to display without credentials."""


def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError) as exc:
        raise OrganizerError(f"Cannot read {path.name}; restore it from backup.") from exc


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
