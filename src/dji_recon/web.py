from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import os
import secrets as secure_random
import threading
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import load_config
from .pipeline import PipelineEvent, RuntimeSecrets, run_pipeline
from .tunnel import PublicTunnel
from .util import redact, utc_now
from .util import redact_text


ROOT = Path(__file__).resolve().parents[2]


class RuntimeState:
    def __init__(self, base_config: dict[str, Any]) -> None:
        self.base_config = base_config
        self.password_salt: bytes | None = None
        self.password_hash: bytes | None = None
        self.status = "setup_required"
        self.events: list[dict[str, Any]] = []
        self.pipeline_state: dict[str, Any] = {}
        self.config_summary: dict[str, Any] = {}
        self.last_error: str | None = None
        self.pipeline_thread: threading.Thread | None = None
        self.pending_upload = False
        self.public_url: str | None = None
        self._run_config: dict[str, Any] | None = None
        self._run_secrets: RuntimeSecrets | None = None
        self._lock = threading.RLock()

    @property
    def setup_complete(self) -> bool:
        return self.password_hash is not None

    def set_password(self, password: str) -> None:
        self.password_salt = os.urandom(16)
        self.password_hash = hashlib.scrypt(password.encode(), salt=self.password_salt, n=2**15, r=8, p=1)
        self.status = "ready"

    def check_password(self, password: str) -> bool:
        if self.password_salt is None or self.password_hash is None:
            return False
        candidate = hashlib.scrypt(password.encode(), salt=self.password_salt, n=2**15, r=8, p=1)
        return hmac.compare_digest(candidate, self.password_hash)

    def add_event(self, event: PipelineEvent) -> None:
        with self._lock:
            self.events.append(vars(event))
            self.events = self.events[-500:]

    def start(self, config: dict[str, Any], runtime_secrets: RuntimeSecrets) -> None:
        with self._lock:
            if self.pipeline_thread and self.pipeline_thread.is_alive():
                raise RuntimeError("a pipeline run is already active")
            self.status = "running"
            self.events = []
            self.last_error = None
            self.pending_upload = False
            self._run_config = config
            self._run_secrets = runtime_secrets
            self.config_summary = redact(config)

        def target() -> None:
            try:
                context = run_pipeline(config, secrets=runtime_secrets, event_callback=self.add_event)
                with self._lock:
                    self.pipeline_state = redact(context.state)
                    upload = context.state.get("stages", {}).get("upload", {})
                    self.pending_upload = upload.get("status") == "awaiting_confirmation"
                    self.status = "awaiting_upload_confirmation" if self.pending_upload else "complete"
            except Exception as error:
                with self._lock:
                    self.status = "failed"
                    self.last_error = redact_text(str(error), runtime_secrets.values())
                    state_file = Path(config["workspace"]).resolve() / ".pipeline" / "state.json"
                    if state_file.exists():
                        self.pipeline_state = redact(json.loads(state_file.read_text(encoding="utf-8")))

        self.pipeline_thread = threading.Thread(target=target, name="dji-recon-pipeline", daemon=True)
        self.pipeline_thread.start()

    def confirm_upload(self) -> None:
        if not self.pending_upload or not self._run_config or not self._run_secrets:
            raise RuntimeError("there is no successful run awaiting upload confirmation")
        config = self._run_config
        runtime_secrets = self._run_secrets
        self.status = "uploading"

        def target() -> None:
            try:
                context = run_pipeline(
                    config,
                    secrets=runtime_secrets,
                    from_stage="upload",
                    to_stage="upload",
                    force_stages={"upload"},
                    confirm_upload=True,
                    event_callback=self.add_event,
                )
                with self._lock:
                    self.pipeline_state = redact(context.state)
                    self.pending_upload = False
                    self.status = "complete"
                    self._run_secrets = None
            except Exception as error:
                with self._lock:
                    self.status = "failed"
                    self.last_error = redact_text(str(error), runtime_secrets.values())

        self.pipeline_thread = threading.Thread(target=target, name="dji-recon-upload", daemon=True)
        self.pipeline_thread.start()


def create_app(config_path: Path) -> FastAPI:
    base_config = load_config(config_path)
    state = RuntimeState(base_config)
    app = FastAPI(title="DJI Reconstruction", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(SessionMiddleware, secret_key=secure_random.token_hex(32), same_site="lax", https_only=False)
    app.mount("/static", StaticFiles(directory=ROOT / "web" / "static"), name="static")
    app.state.runtime = state

    def page(name: str, error: str = "") -> str:
        return (ROOT / "web" / "templates" / name).read_text(encoding="utf-8").replace("{{ERROR}}", error)

    def authenticated(request: Request) -> bool:
        return state.setup_complete and bool(request.session.get("authenticated"))

    @app.get("/", response_class=HTMLResponse)
    async def root(request: Request) -> HTMLResponse:
        if not state.setup_complete:
            return RedirectResponse("/setup", 303)
        if not authenticated(request):
            return RedirectResponse("/login", 303)
        return HTMLResponse(page("workspace.html"))

    @app.get("/setup", response_class=HTMLResponse)
    async def setup_page() -> HTMLResponse:
        if state.setup_complete:
            return HTMLResponse("Not found", 404)
        return HTMLResponse(page("setup.html"))

    @app.post("/setup")
    async def setup(password: str = Form(...), confirm: str = Form(...)) -> RedirectResponse | HTMLResponse:
        if state.setup_complete:
            return HTMLResponse("Not found", 404)
        if password != confirm or len(password) < 10:
            return HTMLResponse(page("setup.html", "Passwords must match and contain at least 10 characters."), 400)
        state.set_password(password)
        return RedirectResponse("/login", 303)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page() -> HTMLResponse:
        if not state.setup_complete:
            return RedirectResponse("/setup", 303)
        return HTMLResponse(page("login.html"))

    @app.post("/login")
    async def login(request: Request, password: str = Form(...)) -> RedirectResponse | HTMLResponse:
        if state.check_password(password):
            request.session.clear()
            request.session["authenticated"] = True
            return RedirectResponse("/", 303)
        return HTMLResponse(page("login.html", "Invalid password."), 401)

    @app.post("/logout")
    async def logout(request: Request) -> RedirectResponse:
        request.session.clear()
        return RedirectResponse("/login", 303)

    @app.get("/api/status")
    async def status(request: Request) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse({"error": "authentication required"}, 401)
        return JSONResponse(
            {
                "status": state.status,
                "events": state.events,
                "pipeline": state.pipeline_state,
                "config": state.config_summary,
                "error": state.last_error,
                "pending_upload": state.pending_upload,
                "public_url": state.public_url,
                "timestamp": utc_now(),
            }
        )

    @app.post("/api/run")
    async def start_run(
        request: Request,
        input_type: str = Form(...),
        input_path: str = Form(""),
        input_url: str = Form(""),
        capture_mode: str = Form(...),
        subset_limit: str = Form(""),
        medium_vram_gb: float = Form(8.0),
        upload_backend: str = Form("none"),
        output_destination: str = Form("dji-recon"),
        mega_email: str = Form(""),
        mega_password: str = Form(""),
        drive_token: str = Form(""),
    ) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse({"error": "authentication required"}, 401)
        if input_type not in {"local", "mega_public", "rclone"}:
            return JSONResponse({"error": "invalid input type"}, 400)
        if capture_mode not in state.base_config["capture_modes"]:
            return JSONResponse({"error": "invalid capture mode"}, 400)
        if upload_backend not in {"none", "mega", "gdrive"}:
            return JSONResponse({"error": "invalid upload backend"}, 400)
        config = copy.deepcopy(state.base_config)
        config["capture_mode"] = capture_mode
        config["input"].update(
            {
                "type": input_type,
                "path": input_path or config["input"].get("path"),
                "rclone_remote": input_path if input_type == "rclone" else None,
                "url": None,
                "subset_limit": int(subset_limit) if subset_limit.strip() else None,
            }
        )
        config["quality"]["medium_vram_gb"] = medium_vram_gb
        config["upload"].update({"backend": upload_backend, "destination": output_destination})
        runtime_secrets = RuntimeSecrets(
            mega_email=mega_email or None,
            mega_password=mega_password or None,
            drive_token=drive_token or None,
            input_folder_url=input_url or None,
        )
        try:
            state.start(config, runtime_secrets)
        except (RuntimeError, ValueError) as error:
            return JSONResponse({"error": str(error)}, 409)
        return JSONResponse({"status": "started"}, 202)

    @app.post("/api/upload/confirm")
    async def confirm_upload(request: Request) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse({"error": "authentication required"}, 401)
        try:
            state.confirm_upload()
            return JSONResponse({"status": "uploading"}, 202)
        except RuntimeError as error:
            return JSONResponse({"error": str(error)}, 409)

    @app.get("/api/rclone/drive-instructions")
    async def drive_instructions(request: Request) -> JSONResponse:
        if not authenticated(request):
            return JSONResponse({"error": "authentication required"}, 401)
        return JSONResponse(
            {
                "steps": [
                    "Install rclone on the computer with your local browser.",
                    "Run: rclone authorize drive",
                    "Complete Google OAuth in the browser window opened by rclone.",
                    "Copy only the resulting JSON token into the authenticated token field. It is kept for this runtime only.",
                ]
            }
        )

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="DJI reconstruction runtime UI")
    parser.add_argument("--config", type=Path, default=Path("configs/default.yaml"))
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--public", action="store_true", help="start the documented FRP tunnel with Cloudflare fallback")
    args = parser.parse_args(argv)
    app = create_app(args.config)
    config = app.state.runtime.base_config
    host = args.host or config["web"].get("bind_host", "127.0.0.1")
    port = args.port or int(config["web"].get("port", 7860))
    tunnel = None
    if args.public:
        tunnel = PublicTunnel(
            config["web"].get("transport_host", "163.61.236.112"),
            int(config["web"].get("broker_port", 7001)),
            int(config["web"].get("frp_port", 7000)),
            port,
            print,
        )
        app.state.runtime.public_url = tunnel.start()
        print(f"Public setup URL: {app.state.runtime.public_url}/setup")
        host = "0.0.0.0"
    try:
        uvicorn.run(app, host=host, port=port, access_log=False)
    finally:
        if tunnel:
            tunnel.stop()


if __name__ == "__main__":
    main()
