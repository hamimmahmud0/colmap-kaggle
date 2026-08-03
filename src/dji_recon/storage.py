from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Callable

from .util import CommandError, run_command


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
        mega_cmd = shutil.which("mega-cmd")
        if not mega_cmd:
            raise RuntimeError(
                "mega-cmd is required for a private-key MEGA folder download without exposing the URL in process arguments"
            )
        url = source.get("url")
        if not url:
            raise ValueError("input.url is required for mega_public")
        # The link is supplied over stdin to the interactive shell, never argv.
        run_command([mega_cmd], stdin=f"get {url} {destination}\nquit\n", log=log)
        return
    if kind == "rclone":
        remote = source.get("rclone_remote")
        if not remote:
            raise ValueError("input.rclone_remote is required")
        run_command(["rclone", "copy", remote, str(destination), "--progress"], log=log)
        return
    raise ValueError(f"unsupported input.type {kind!r}")
