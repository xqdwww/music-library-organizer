from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .applier import apply, load_plan
from .errors import OrganizerError
from .media import extract_cover
from .planner import create_plan, scan, write_json


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="music-organizer", description="Safely organize a local music library")
    value.add_argument("--version", action="version", version="music-organizer 0.1.0")
    commands = value.add_subparsers(dest="command", required=True)
    scan_cmd = commands.add_parser("scan", help="read metadata without changing files")
    scan_cmd.add_argument("root", type=Path)
    scan_cmd.add_argument("--output", type=Path)
    scan_cmd.add_argument("--force", action="store_true")
    plan_cmd = commands.add_parser("plan", help="create a deterministic organization plan")
    plan_cmd.add_argument("root", type=Path)
    plan_cmd.add_argument("--metadata", type=Path)
    plan_cmd.add_argument("--output", type=Path, required=True)
    plan_cmd.add_argument("--force", action="store_true")
    apply_cmd = commands.add_parser("apply", help="preview a plan; --execute writes copies")
    apply_cmd.add_argument("plan", type=Path)
    apply_cmd.add_argument("--destination", type=Path, required=True)
    apply_cmd.add_argument("--cover", type=Path)
    apply_cmd.add_argument("--execute", action="store_true")
    art_cmd = commands.add_parser("artwork", help="extract embedded local cover art")
    art_cmd.add_argument("source", type=Path)
    art_cmd.add_argument("--output", type=Path, required=True)
    art_cmd.add_argument("--force", action="store_true")
    return value


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "scan":
            result = scan(args.root)
            if args.output:
                write_json(result, args.output, args.force)
            else:
                emit(result)
        elif args.command == "plan":
            result = create_plan(args.root, args.metadata)
            write_json(result, args.output, args.force)
            emit({"status": "planned", "items": len(result["items"]), "output": str(args.output)})
        elif args.command == "apply":
            emit(apply(load_plan(args.plan), args.destination, args.execute, args.cover))
        else:
            emit(extract_cover(args.source, args.output, args.force))
        return 0
    except OrganizerError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
