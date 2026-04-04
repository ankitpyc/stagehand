"""
cli.py — `stagehand` command-line interface.

Commands:
    stagehand list                       List all active checkpoints
    stagehand status <pipeline_id>       Show stage-by-stage status
    stagehand reset  <pipeline_id>       Clear checkpoint (start fresh on next run)
    stagehand runs   <pipeline_id>       Show run history
    stagehand version                    Print version
"""

import sys
from . import __version__
from . import checkpoint as ckpt
from .pipeline import Pipeline


def main():
    args = sys.argv[1:]

    if not args or args[0] in ("-h", "--help"):
        _print_help()
        return

    cmd = args[0]

    if cmd == "version":
        print(f"stagehand {__version__}")

    elif cmd == "list":
        active = ckpt.list_active()
        if not active:
            print("No active checkpoints.")
            return
        _header("PIPELINE", "STARTED", "DONE", "FAILED", "PENDING")
        for r in active:
            _row(r["pipeline_id"][:50], r["started_at"][:16], r["done"], r["failed"], r["pending"])

    elif cmd == "status":
        if len(args) < 2:
            print("Usage: stagehand status <pipeline_id>", file=sys.stderr)
            sys.exit(1)
        p = Pipeline(args[1])
        p.status()

    elif cmd == "reset":
        if len(args) < 2:
            print("Usage: stagehand reset <pipeline_id>", file=sys.stderr)
            sys.exit(1)
        p = Pipeline(args[1])
        p.reset()

    elif cmd == "runs":
        if len(args) < 2:
            print("Usage: stagehand runs <pipeline_id>", file=sys.stderr)
            sys.exit(1)
        runs = ckpt.list_runs(args[1])
        if not runs:
            print(f"No run history for '{args[1]}'")
            return
        _header("RUN", "STARTED", "DONE/TOTAL", "FAILED STAGES")
        for r in runs:
            failed = ", ".join(r["failed_stages"]) or "—"
            _row(r["run"], r["started_at"][:16], f"{r['done']}/{r['total']}", failed)

    else:
        print(f"Unknown command: {cmd}", file=sys.stderr)
        _print_help()
        sys.exit(1)


def _print_help():
    print("""stagehand — minimal pipeline runner with checkpointing

Usage:
  stagehand list                   List all active (incomplete) pipelines
  stagehand status <pipeline_id>   Show stage status for a pipeline
  stagehand reset  <pipeline_id>   Clear checkpoint (restart from scratch)
  stagehand runs   <pipeline_id>   Show run history
  stagehand version                Print version

Checkpoint directory: $STAGEHAND_DIR (default: ~/.stagehand)
""")


def _header(*cols):
    print("  ".join(f"{c:<20}" for c in cols))
    print("  ".join("-" * 20 for _ in cols))


def _row(*cols):
    print("  ".join(f"{str(c):<20}" for c in cols))
