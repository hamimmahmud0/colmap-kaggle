from __future__ import annotations

import json
import os
import re
import signal
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


class TunnelError(RuntimeError):
    pass


def _json_request(url: str, *, method: str = "GET", body: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError) as error:
        raise TunnelError(f"broker request failed: {error}") from error


class PublicTunnel:
    def __init__(self, host: str, broker_port: int, frp_port: int, local_port: int, log: Callable[[str], None]) -> None:
        self.host = host
        self.base = f"http://{host}:{broker_port}"
        self.frp_port = frp_port
        self.local_port = local_port
        self.log = log
        self.process: subprocess.Popen[str] | None = None
        self.room_id: str | None = None
        self.room_secret: str | None = None
        self.tunnel_id: str | None = None
        self._stop = threading.Event()
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self.public_url: str | None = None

    def start(self) -> str:
        try:
            url = self._start_frp()
        except Exception as error:
            self.log(f"primary FRP tunnel unavailable: {error}; trying Cloudflare fallback")
            self.stop()
            url = self._start_cloudflare()
        self.log(f"Public setup URL: {url}/setup")
        threading.Thread(target=self._drain_process_output, daemon=True).start()
        return url

    def _start_frp(self) -> str:
        frpc = shutil.which("frpc")
        if not frpc:
            raise TunnelError("frpc executable is missing")
        room = _json_request(f"{self.base}/rooms", method="POST", body={"name": "dji-recon"})
        self.room_id = str(room["room_id"])
        self.room_secret = str(room["room_secret"])
        tunnel = _json_request(
            f"{self.base}/rooms/{self.room_id}/tunnels",
            method="POST",
            body={"room_secret": self.room_secret, "name": "web"},
        )
        self.tunnel_id = str(tunnel["tunnel_id"])
        remote_port = int(tunnel["remote_port"])
        self._temporary = tempfile.TemporaryDirectory(prefix="dji-recon-frp-")
        config = Path(self._temporary.name) / "frpc.toml"
        config.write_text(
            f'serverAddr = "{self.host}"\n'
            f"serverPort = {self.frp_port}\n\n"
            "[[proxies]]\n"
            'name = "dji-recon-web"\n'
            'type = "tcp"\n'
            'localIP = "127.0.0.1"\n'
            f"localPort = {self.local_port}\n"
            f"remotePort = {remote_port}\n",
            encoding="utf-8",
        )
        os.chmod(config, 0o600)
        self.process = subprocess.Popen(
            [frpc, "-c", str(config)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        time.sleep(2)
        if self.process.poll() is not None:
            output = self.process.stdout.read().strip() if self.process.stdout else ""
            detail = output.splitlines()[-1] if output else f"exit code {self.process.returncode}"
            detail = re.sub(r"\x1b\[[0-9;]*m", "", detail)
            raise TunnelError(f"frpc exited before the tunnel became ready: {detail}")
        threading.Thread(target=self._heartbeat, daemon=True).start()
        self.public_url = f"http://{self.host}:{remote_port}"
        return self.public_url

    def _drain_process_output(self) -> None:
        process = self.process
        if not process or not process.stdout:
            return
        for line in process.stdout:
            clean = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
            if clean:
                self.log(clean)

    def _heartbeat(self) -> None:
        while not self._stop.wait(20):
            if not self.room_id or not self.tunnel_id or not self.room_secret:
                return
            try:
                _json_request(
                    f"{self.base}/rooms/{self.room_id}/tunnels/{self.tunnel_id}/heartbeat",
                    method="POST",
                    headers={"X-Room-Secret": self.room_secret},
                )
            except TunnelError as error:
                self.log(f"FRP heartbeat failed: {error}")

    def _start_cloudflare(self) -> str:
        cloudflared = shutil.which("cloudflared")
        if not cloudflared:
            raise TunnelError("both frpc and cloudflared are unavailable")
        self.process = subprocess.Popen(
            [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{self.local_port}", "--no-autoupdate"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=True,
        )
        assert self.process.stdout is not None
        deadline = time.monotonic() + 45
        pattern = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")
        while time.monotonic() < deadline:
            line = self.process.stdout.readline()
            if not line and self.process.poll() is not None:
                break
            if match := pattern.search(line):
                self.public_url = match.group(0)
                return self.public_url
        raise TunnelError("Cloudflare tunnel did not publish a URL")

    def stop(self) -> None:
        self._stop.set()
        if self.room_id and self.room_secret:
            try:
                _json_request(
                    f"{self.base}/rooms/{self.room_id}",
                    method="DELETE",
                    headers={"X-Room-Secret": self.room_secret},
                )
            except TunnelError:
                pass
        if self.process and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=5)
        if self._temporary:
            self._temporary.cleanup()
