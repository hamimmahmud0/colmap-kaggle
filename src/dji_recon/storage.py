from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import re
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable

from .util import CommandError, run_command


def _mega_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _parse_mega_paths(output: str) -> list[str]:
    paths = []
    for raw_line in output.splitlines():
        line = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", "", raw_line).strip()
        if line.startswith("/") and line.lower().endswith(".dng"):
            paths.append(line)
    return sorted(set(paths), key=str.casefold)


def _mega_export_relative(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise ValueError(f"unexpected exported-folder path: {path!r}")
    return "/".join(parts[1:])


def _mega_script(commands: list[str], log: Callable[[str], None], *, capture: bool = False) -> str:
    executable = shutil.which("mega-cmd")
    if not executable:
        raise RuntimeError("mega-cmd is required for MEGA folder input")
    with tempfile.TemporaryDirectory(prefix="dji-recon-megacmd-") as home:
        environment = os.environ.copy()
        environment["HOME"] = home
        payload = "\n".join([*commands, "logout", "quit"]) + "\n"
        process = subprocess.run(
            [executable],
            input=payload,
            capture_output=True,
            text=True,
            errors="replace",
            env=environment,
            check=False,
        )
        output = f"{process.stdout}\n{process.stderr}"
        if not capture:
            completed = 0
            for line in output.splitlines():
                if "Download finished:" in line:
                    completed += 1
                    log(line)
                elif " ERR " in line:
                    log(line)
            log(f"MEGA download session completed {completed} file transfer(s)")
        if process.returncode:
            raise RuntimeError(f"MEGAcmd session failed with exit code {process.returncode}")
        return output


class EphemeralRclone(AbstractContextManager["EphemeralRclone"]):
    def __init__(self) -> None:
        self._temporary = tempfile.TemporaryDirectory(prefix="dji-recon-rclone-")
        self.path = Path(self._temporary.name) / "rclone.conf"
        self.path.touch(mode=0o600)

    def __exit__(self, *args: object) -> None:
        self._temporary.cleanup()

    def add_mega(self, email: str, password: str) -> str:
        result = subprocess.run(
            ["rclone", "obscure", "-"],
            input=password,
            capture_output=True,
            text=True,
            check=True,
        )
        self.path.write_text(
            "[runtime-mega]\n"
            "type = mega\n"
            f"user = {email}\n"
            f"pass = {result.stdout.strip()}\n",
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)
        return "runtime-mega"

    def add_drive(self, token: str) -> str:
        parsed = json.loads(token)
        if not isinstance(parsed, dict) or "access_token" not in parsed:
            raise ValueError("Google Drive token must be rclone token JSON")
        self.path.write_text(
            "[runtime-drive]\n"
            "type = drive\n"
            "scope = drive\n"
            f"token = {json.dumps(parsed, separators=(',', ':'))}\n",
            encoding="utf-8",
        )
        os.chmod(self.path, 0o600)
        return "runtime-drive"

    def validate(self, remote: str, log: Callable[[str], None]) -> None:
        run_command(["rclone", "--config", str(self.path), "lsd", f"{remote}:", "--max-depth", "1"], log=log)

    def copy_to(self, source: Path, remote: str, destination: str, log: Callable[[str], None]) -> None:
        run_command(
            ["rclone", "--config", str(self.path), "copy", str(source), f"{remote}:{destination}", "--progress"],
            log=log,
        )


def download_input(config: dict[str, Any], destination: Path, log: Callable[[str], None]) -> None:
    source = config["input"]
    kind = source.get("type", "local")
    destination.mkdir(parents=True, exist_ok=True)
    if kind == "local":
        local = Path(source["path"]).expanduser().resolve()
        if not local.is_dir():
            raise FileNotFoundError(f"input directory does not exist: {local}")
        # Sources remain untouched. Symlinks avoid a duplicate local copy.
        for item in local.rglob("*"):
            if item.is_file():
                relative = item.relative_to(local)
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.symlink_to(item)
        return
    if kind == "mega_public":
        url = source.get("url")
        if not url:
            raise ValueError("input.url is required for mega_public")
        # The link and key are supplied over stdin to an isolated interactive
        # shell, never argv. Selecting before `get` protects small runtimes
        # from downloading an entire large capture merely to make a subset.
        inventory = _mega_script([f"login {_mega_quote(url)}", "find / --type=f"], log, capture=True)
        dng_paths = _parse_mega_paths(inventory)
        if not dng_paths:
            raise RuntimeError("MEGA public folder contains no discoverable DNG files")
        limit = source.get("subset_limit")
        selected = dng_paths[: int(limit)] if limit else dng_paths
        commands = [f"login {_mega_quote(url)}"]
        commands.extend(
            f"get {_mega_quote(_mega_export_relative(path))} {_mega_quote(str(destination))}"
            for path in selected
        )
        _mega_script(commands, log)
        return
    if kind == "rclone":
        remote = source.get("rclone_remote")
        if not remote:
            raise ValueError("input.rclone_remote is required")
        run_command(["rclone", "copy", remote, str(destination), "--progress"], log=log)
        return
    raise ValueError(f"unsupported input.type {kind!r}")
