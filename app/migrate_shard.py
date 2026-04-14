#!/usr/bin/env python3
"""Migrate a flat image/cache directory into the sharded layout.

Moves every file in the top level of BASE_DIR into its shard path
(BASE_DIR/ab/cd/<file>). Idempotent and safe to re-run: files already
inside a shard subdirectory are skipped. Intended to be run with the
app stopped (or uploads paused) so no new files are written mid-move.

Usage:
    python migrate_shard.py /images
    python migrate_shard.py /cache --dry-run
"""
import argparse
import os
import sys

SHARD_SEGMENT_LEN = 2
SHARD_DEPTH = 2


def shard_segments(filename: str) -> list[str]:
    key = filename[: SHARD_SEGMENT_LEN * SHARD_DEPTH]
    return [key[i * SHARD_SEGMENT_LEN : (i + 1) * SHARD_SEGMENT_LEN] for i in range(SHARD_DEPTH)]


def migrate(base_dir: str, dry_run: bool) -> tuple[int, int, int]:
    moved = 0
    skipped = 0
    failed = 0
    with os.scandir(base_dir) as it:
        for entry in it:
            if not entry.is_file(follow_symlinks=False):
                skipped += 1
                continue
            segments = shard_segments(entry.name)
            if any(not s for s in segments):
                print(f"skip (name too short for shard): {entry.name}", file=sys.stderr)
                skipped += 1
                continue
            target_dir = os.path.join(base_dir, *segments)
            target = os.path.join(target_dir, entry.name)
            if os.path.exists(target):
                print(f"skip (target exists): {entry.path}", file=sys.stderr)
                skipped += 1
                continue
            if dry_run:
                print(f"would move {entry.path} -> {target}")
                moved += 1
                continue
            try:
                os.makedirs(target_dir, exist_ok=True)
                os.rename(entry.path, target)
            except OSError as exc:
                print(f"failed {entry.path} -> {target}: {exc}", file=sys.stderr)
                failed += 1
                continue
            moved += 1
    return moved, skipped, failed


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("base_dir", help="Directory to migrate (e.g. /images or /cache)")
    p.add_argument("--dry-run", action="store_true", help="Show planned moves without touching disk")
    args = p.parse_args()

    if not os.path.isdir(args.base_dir):
        print(f"not a directory: {args.base_dir}", file=sys.stderr)
        sys.exit(1)

    moved, skipped, failed = migrate(args.base_dir, args.dry_run)
    verb = "would move" if args.dry_run else "moved"
    print(f"{verb} {moved} file(s), skipped {skipped}, failed {failed}")
    if failed:
        sys.exit(2)


if __name__ == "__main__":
    main()
