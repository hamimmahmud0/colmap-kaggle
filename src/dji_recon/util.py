from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


SECRET_KEYS = re.compile(
    r"(password|passwd|secret|token|cookie|private.?key|folder.?url|oauth|rclone_config)",
    re.IGNORECASE,
)
SECRET_TEXT = [
    re.compile(r"(?i)(password|passwd|secret|token)\s*[=:]\s*\S+"),
    re.compile(r"https://mega\.nz/(?:folder|file)/[^\s#]+#[^\s]+", re.IGNORECASE),
    re.compile(r"(?i)(authorization:\s*(?:bearer|basic))\s+\S+"),
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_text(value: str, extra_values: Iterable[str] = ()) -> str:
    result = value
    for secret in extra_values:
        if secret:
            result = result.replace(secret, "[REDACTED]")
    for pattern in SECRET_TEXT:
        result = pattern.sub(lambda m: f"{m.group(1)}=[REDACTED]" if m.lastindex else "[REDACTED]", result)
    return result


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SECRET_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(data: Any) -> str:
    encoded = json.dumps(redact(data), sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def file_identity(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sha256": sha256_file(path),
    }


def executable_version(name: str, args: list[str] | None = None) -> str | None:
    executable = shutil.which(name)
    if not executable:
        return None
    try:
        result = subprocess.run(
            [executable, *(args or ["--version"])],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
        text = (result.stdout or result.stderr).strip().splitlines()
        return text[0] if text else executable
    except (OSError, subprocess.TimeoutExpired):
        return executable


class CommandError(RuntimeError):
    pass


def run_command(
    argv: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    log: Callable[[str], None] | None = None,
    stdin: str | None = None,
) -> None:
    if not argv:
        raise ValueError("empty command")
    display = " ".join(shlex_quote(item) for item in argv)
    if log:
        log(f"$ {redact_text(display)}")
    process = subprocess.Popen(
        argv,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    if stdin is not None and process.stdin:
        process.stdin.write(stdin)
        process.stdin.close()
    assert process.stdout is not None
    for line in process.stdout:
        if log:
            log(redact_text(line.rstrip()))
    code = process.wait()
    if code:
        raise CommandError(f"command failed with exit code {code}: {redact_text(display)}")


def shlex_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)
