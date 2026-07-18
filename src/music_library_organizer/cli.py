from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from .album_prune.calibration import create_read_only_baseline, verify_read_only_baseline
from .album_prune.calibration_webui import serve_calibration
from .album_prune.scoring import ScoringConfig
from .album_prune.service import DEFAULT_USER_AGENT, AlbumPruneService
from .album_prune.webui import serve as serve_album_review
from .applier import apply, load_plan
from .errors import OrganizerError
from .media import extract_cover
from .planner import create_plan, scan, write_json


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(prog="music-organizer", description="Safely organize a local music library")
    value.add_argument("--version", action="version", version="music-organizer 0.2.0")
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
    prune = commands.add_parser("album-prune", help="review public album ratings and quarantine selected albums")
    prune.add_argument("--config", type=Path)
    prune.add_argument("--state-root", type=Path)
    prune_commands = prune.add_subparsers(dest="prune_command", required=True)
    prune_scan = prune_commands.add_parser("scan", help="scan albums and public ratings without changing media")
    prune_scan.add_argument("root", type=Path, nargs="?")
    prune_scan.add_argument("--limit", type=int)
    prune_scan.add_argument("--album-path", action="append", default=[])
    prune_scan.add_argument("--offline", action="store_true")
    prune_scan.add_argument("--refresh", action="store_true")
    prune_scan.add_argument("--no-ratings", action="store_true")
    prune_scan.add_argument("--professional", action="store_true")
    prune_candidates = prune_commands.add_parser("candidates", help="list review candidates")
    prune_candidates.add_argument("--threshold", type=float)
    prune_candidates.add_argument("--output", type=Path)
    prune_protect = prune_commands.add_parser("protect", help="permanently protect one canonical album")
    prune_protect.add_argument("album_id")
    prune_protect.add_argument("--reason", default="user protected")
    prune_select = prune_commands.add_parser("select", help="create an explicit user selection")
    prune_select.add_argument("album_ids", nargs="+")
    prune_plan = prune_commands.add_parser("plan", help="build a hashed quarantine preview from a selection")
    prune_plan.add_argument("--selection-id", required=True)
    prune_plan.add_argument("--library-root", type=Path)
    prune_plan.add_argument("--quarantine-root", type=Path)
    prune_plan.add_argument("--output", type=Path)
    prune_apply = prune_commands.add_parser("apply", help="move a confirmed plan into quarantine")
    prune_apply.add_argument("--batch-id", required=True)
    prune_apply.add_argument("--confirm", required=True)
    prune_rollback = prune_commands.add_parser("rollback", help="restore one verified quarantine batch")
    prune_rollback.add_argument("--batch-id", required=True)
    prune_rollback.add_argument("--confirm", required=True)
    prune_recover = prune_commands.add_parser("recover", help="roll back a batch interrupted while applying")
    prune_recover.add_argument("--batch-id", required=True)
    prune_recover.add_argument("--confirm", required=True)
    prune_purge = prune_commands.add_parser("purge", help="permanently purge one verified quarantine batch")
    prune_purge.add_argument("--batch-id", required=True)
    prune_purge.add_argument("--confirm", required=True)
    prune_commands.add_parser("batches", help="list cleanup batches")
    baseline = prune_commands.add_parser("calibration-baseline", help="create or verify a read-only library baseline")
    baseline.add_argument("action", choices=("create", "verify"))
    baseline.add_argument("--library-root", type=Path)
    baseline.add_argument("--output", type=Path)
    baseline.add_argument("--seed", type=int, default=20260718)
    sample = prune_commands.add_parser("calibration-sample", help="create a reproducible stratified sample")
    sample.add_argument("--size", type=int, default=140)
    sample.add_argument("--seed", type=int, default=20260718)
    enrich = prune_commands.add_parser("calibration-enrich", help="enrich one calibration sample with ratings")
    enrich.add_argument("--batch-id", required=True)
    enrich.add_argument("--library-root", type=Path)
    enrich.add_argument("--offline", action="store_true")
    enrich.add_argument("--refresh", action="store_true")
    enrich_library = prune_commands.add_parser(
        "calibration-enrich-library", help="incrementally enrich all stored albums without rescanning media"
    )
    enrich_library.add_argument(
        "--source",
        choices=("musicbrainz", "discogs", "official-awards"),
        required=True,
    )
    enrich_library.add_argument("--offline", action="store_true")
    enrich_library.add_argument("--refresh", action="store_true")
    enrich_library.add_argument(
        "--language-status",
        action="append",
        choices=(
            "ZH_CONFIRMED",
            "JA_CONFIRMED",
            "KO_CONFIRMED",
            "HK_TW_CANTONESE",
            "MIXED_CJK",
            "NON_CJK",
            "UNKNOWN_CJK",
        ),
        default=[],
        help="limit enrichment to one or more evidence-based language routes",
    )
    enrich_library.add_argument(
        "--category",
        action="append",
        choices=("Popular/Rock/Folk", "Classical", "Jazz", "Other"),
        default=[],
        help="limit enrichment to one or more local album categories",
    )
    enrich_library.add_argument(
        "--album-id",
        action="append",
        default=[],
        help="limit enrichment to one or more stable local album IDs",
    )
    prune_commands.add_parser("calibration-stats", help="summarize the current full-library review state")
    prune_commands.add_parser("calibration-scope", help="assign local-only language and rating scope")
    beets_scope = prune_commands.add_parser(
        "calibration-import-beets-scope", help="import local language/country/script scope metadata from beets"
    )
    beets_scope.add_argument("--beets-db", type=Path, required=True)
    beets_scope.add_argument("--library-root", type=Path)
    prune_commands.add_parser("calibration-report", help="analyze labelled decisions across fixed thresholds")
    policy = prune_commands.add_parser("calibration-policy", help="write a disabled personal policy template")
    policy.add_argument("--output", type=Path)
    policy.add_argument("--batch-id", default="")
    apply_policy = prune_commands.add_parser(
        "personal-policy-apply",
        help="verify one completed calibration batch and enable its personal candidate policy",
    )
    apply_policy.add_argument("--batch-id", required=True)
    apply_policy.add_argument("--strong-threshold", type=float, default=65)
    apply_policy.add_argument("--review-threshold", type=float, default=70)
    apply_policy.add_argument("--output", type=Path)
    personal_candidates = prune_commands.add_parser(
        "personal-candidates",
        help="generate the active personal candidate groups without changing media",
    )
    personal_candidates.add_argument("--output", type=Path)
    calibration_serve = prune_commands.add_parser("calibration-serve", help="serve the non-destructive calibration UI")
    calibration_serve.add_argument("--batch-id", required=True)
    calibration_serve.add_argument("--host", default="127.0.0.1")
    calibration_serve.add_argument("--port", type=int, default=8767)
    prune_serve = prune_commands.add_parser("serve", help="open the integrated local review control")
    prune_serve.add_argument("--library-root", type=Path)
    prune_serve.add_argument("--quarantine-root", type=Path)
    prune_serve.add_argument("--host", default="127.0.0.1")
    prune_serve.add_argument("--port", type=int, default=8765)
    return value


def emit(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _album_config(path: Path | None) -> dict[str, Any]:
    configured = path or (
        Path(os.environ["MUSIC_ORGANIZER_CONFIG"]) if os.environ.get("MUSIC_ORGANIZER_CONFIG") else None
    )
    if configured is None:
        default = Path.home() / ".config/music-library-organizer/config.local.json"
        configured = default if default.is_file() else None
    if configured is None:
        return {}
    value = json.loads(configured.expanduser().read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("album-prune config must be a JSON object")
    return value


def _configured_path(explicit: Path | None, config: dict[str, Any], key: str) -> Path:
    value = explicit or (Path(str(config[key])) if config.get(key) else None)
    if value is None:
        raise ValueError(f"{key} is required by argument or config")
    return value.expanduser()


def _album_prune(args: argparse.Namespace) -> dict[str, Any] | None:
    config_data = _album_config(args.config)
    scoring = ScoringConfig(
        **{key: config_data[key] for key in ScoringConfig.__dataclass_fields__ if key in config_data}
    )
    state_root = args.state_root or Path(str(config_data.get("state_root", "~/.local/state/music-library-organizer")))
    service = AlbumPruneService(state_root, scoring)
    command = args.prune_command
    if command == "scan":
        root = _configured_path(args.root, config_data, "library_root")
        return service.scan(
            root,
            limit=args.limit,
            album_paths=args.album_path or None,
            offline=args.offline,
            refresh=args.refresh,
            ratings=not args.no_ratings,
            professional=args.professional,
            user_agent=str(config_data.get("musicbrainz_user_agent", DEFAULT_USER_AGENT)),
        )
    if command == "candidates":
        value: Any = {"status": "CANDIDATES_READY", "candidates": service.candidates(args.threshold)}
        if args.output:
            write_json(value, args.output)
            return {"status": "written", "output": str(args.output), "candidates": len(value["candidates"])}
        return value
    if command == "protect":
        return service.protect(args.album_id, args.reason)
    if command == "select":
        return service.select(args.album_ids)
    if command == "plan":
        result = service.plan(
            args.selection_id,
            _configured_path(args.library_root, config_data, "library_root"),
            _configured_path(args.quarantine_root, config_data, "quarantine_root"),
        )
        if args.output:
            write_json({key: value for key, value in result.items() if key != "state_dir"}, args.output)
        return result
    if command == "apply":
        return service.apply(args.batch_id, args.confirm)
    if command == "rollback":
        return service.rollback(args.batch_id, args.confirm)
    if command == "recover":
        return service.recover(args.batch_id, args.confirm)
    if command == "purge":
        return service.purge(args.batch_id, args.confirm)
    if command == "batches":
        return {"status": "BATCHES", "batches": service.batches()}
    if command == "calibration-baseline":
        root = _configured_path(args.library_root, config_data, "library_root")
        output = args.output or service.state_root / "calibration" / "library-baseline.json"
        if args.action == "create":
            return {"status": "BASELINE_CREATED", **create_read_only_baseline(root, output, args.seed)}
        return verify_read_only_baseline(root, output)
    if command == "calibration-sample":
        return service.create_calibration_sample(args.size, args.seed)
    if command == "calibration-enrich":
        root = _configured_path(args.library_root, config_data, "library_root")
        return service.scan(
            root,
            album_paths=service.calibration_album_paths(args.batch_id),
            offline=args.offline,
            refresh=args.refresh,
            user_agent=str(config_data.get("musicbrainz_user_agent", DEFAULT_USER_AGENT)),
        )
    if command == "calibration-enrich-library":
        return service.enrich_existing_library(
            args.source,
            language_statuses=set(args.language_status) or None,
            categories=set(args.category) or None,
            album_ids=set(args.album_id) or None,
            offline=args.offline,
            refresh=args.refresh,
            user_agent=str(config_data.get("musicbrainz_user_agent", DEFAULT_USER_AGENT)),
        )
    if command == "calibration-stats":
        return service.library_statistics()
    if command == "calibration-scope":
        return service.assign_library_rating_scope()
    if command == "calibration-import-beets-scope":
        return service.import_beets_rating_scope(
            args.beets_db,
            _configured_path(args.library_root, config_data, "library_root"),
        )
    if command == "calibration-report":
        return service.calibration_report()
    if command == "calibration-policy":
        output = args.output or service.state_root / "calibration" / "personal_album_pruning_policy.yaml"
        return service.write_policy_template(output, args.batch_id)
    if command == "personal-policy-apply":
        return service.apply_personal_policy(
            args.batch_id,
            args.output,
            strong_threshold=args.strong_threshold,
            review_threshold=args.review_threshold,
        )
    if command == "personal-candidates":
        value = service.personal_candidate_report()
        if args.output:
            write_json(value, args.output)
            return {
                "status": "written",
                "output": str(args.output),
                **value["summary"],
            }
        return value
    if command == "calibration-serve":
        serve_calibration(service, args.batch_id, args.host, args.port)
        return None
    serve_album_review(
        service,
        _configured_path(args.library_root, config_data, "library_root"),
        _configured_path(args.quarantine_root, config_data, "quarantine_root"),
        args.host,
        args.port,
    )
    return None


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "album-prune":
            result = _album_prune(args)
            if result is not None:
                emit(result)
        elif args.command == "scan":
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
    except (OSError, ValueError, KeyError, PermissionError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
