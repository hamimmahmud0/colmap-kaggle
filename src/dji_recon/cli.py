from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .pipeline import STAGES, PipelineEvent, run_pipeline
from .resources import detect_resources
from .util import redact


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="dji-recon", description="Reproducible DJI photogrammetry pipeline")
    result.add_argument("--config", type=Path, default=Path("configs/default.yaml"))
    result.add_argument("--workspace", type=Path, default=None, help="override the workspace directory (e.g. /tmp/recon)")
    result.add_argument("--from-stage", choices=STAGES)
    result.add_argument("--to-stage", choices=STAGES)
    result.add_argument("--force-stage", choices=STAGES, action="append", default=[])
    result.add_argument("--confirm-upload", action="store_true", help="explicitly authorize configured artifact upload")
    result.add_argument("--print-resources", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        config = load_config(args.config)
        if args.workspace is not None:
            config["workspace"] = str(args.workspace)
        if args.print_resources:
            print(json.dumps(redact(detect_resources(Path(config["workspace"]).resolve())), indent=2))
            return 0

        def show(event: PipelineEvent) -> None:
            stage = f"[{event.stage}] " if event.stage else ""
            print(f"{event.timestamp} {stage}{event.level.upper()}: {event.message}", flush=True)

        context = run_pipeline(
            config,
            from_stage=args.from_stage,
            to_stage=args.to_stage,
            force_stages=set(args.force_stage),
            confirm_upload=args.confirm_upload,
            event_callback=show,
        )
        print(f"Pipeline finished. State: {context.state_path}")
        return 0
    except (ConfigError, FileNotFoundError, ValueError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 2
    except Exception as error:
        print(f"Pipeline failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
