#!/usr/bin/env python3
"""Migrate local imgpush images to an S3-compatible storage backend.

Reads S3 configuration from environment variables (same as the app):
  S3_ENDPOINT_URL, S3_ACCESS_KEY_ID, S3_SECRET_ACCESS_KEY,
  S3_BUCKET_NAME, S3_REGION, S3_IMAGES_PREFIX

Usage:
  python scripts/migrate_to_s3.py --images-dir /images
  python scripts/migrate_to_s3.py --images-dir /images --dry-run
  python scripts/migrate_to_s3.py --images-dir /images --verify
"""

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import boto3


def create_client(args: argparse.Namespace) -> boto3.client:
    kwargs = {}
    endpoint = os.environ.get("S3_ENDPOINT_URL", "")
    region = os.environ.get("S3_REGION", "")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    if region:
        kwargs["region_name"] = region

    return boto3.client(
        "s3",
        aws_access_key_id=os.environ["S3_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["S3_SECRET_ACCESS_KEY"],
        **kwargs,
    )


def get_files(images_dir: str) -> list[str]:
    files = []
    for entry in os.scandir(images_dir):
        if entry.is_file():
            files.append(entry.name)
    return sorted(files)


def upload_file(client, bucket: str, prefix: str, images_dir: str, filename: str) -> tuple[str, int]:
    local_path = os.path.join(images_dir, filename)
    s3_key = prefix + filename
    size = os.path.getsize(local_path)
    client.upload_file(local_path, bucket, s3_key)
    return filename, size


def verify_file(client, bucket: str, prefix: str, images_dir: str, filename: str) -> tuple[str, bool]:
    local_path = os.path.join(images_dir, filename)
    s3_key = prefix + filename
    local_size = os.path.getsize(local_path)

    try:
        response = client.head_object(Bucket=bucket, Key=s3_key)
        remote_size = response["ContentLength"]
        return filename, local_size == remote_size
    except Exception:
        return filename, False


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate local images to S3-compatible storage")
    parser.add_argument("--images-dir", required=True, help="Path to local images directory")
    parser.add_argument("--dry-run", action="store_true", help="List files without uploading")
    parser.add_argument("--verify", action="store_true", help="Verify uploaded files match local sizes")
    parser.add_argument("--workers", type=int, default=8, help="Number of parallel upload workers (default: 8)")
    args = parser.parse_args()

    bucket = os.environ.get("S3_BUCKET_NAME", "")
    prefix = os.environ.get("S3_IMAGES_PREFIX", "images/")

    if not bucket:
        print("Error: S3_BUCKET_NAME environment variable is required", file=sys.stderr)
        sys.exit(1)

    files = get_files(args.images_dir)
    total_files = len(files)

    if total_files == 0:
        print("No files found to migrate.")
        return

    print(f"Found {total_files} files in {args.images_dir}")

    if args.dry_run:
        total_size = 0
        for filename in files:
            size = os.path.getsize(os.path.join(args.images_dir, filename))
            total_size += size
            print(f"  {filename} ({size:,} bytes)")
        print(f"\nTotal: {total_files} files, {total_size:,} bytes ({total_size / 1024 / 1024:.1f} MB)")
        print("(dry run — no files uploaded)")
        return

    client = create_client(args)

    if args.verify:
        print("Verifying uploaded files...")
        ok = 0
        failed = []
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(verify_file, client, bucket, prefix, args.images_dir, f): f for f in files
            }
            for future in as_completed(futures):
                filename, match = future.result()
                if match:
                    ok += 1
                else:
                    failed.append(filename)
                print(f"\r  Verified {ok + len(failed)}/{total_files}", end="", flush=True)

        print()
        if failed:
            print(f"\n{len(failed)} files FAILED verification:")
            for f in failed:
                print(f"  {f}")
            sys.exit(1)
        else:
            print(f"All {ok} files verified successfully.")
        return

    # Upload
    print(f"Uploading to s3://{bucket}/{prefix} with {args.workers} workers...")
    uploaded = 0
    total_bytes = 0
    errors = []

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {
            executor.submit(upload_file, client, bucket, prefix, args.images_dir, f): f for f in files
        }
        for future in as_completed(futures):
            try:
                filename, size = future.result()
                uploaded += 1
                total_bytes += size
                print(f"\r  Uploaded {uploaded}/{total_files} ({total_bytes / 1024 / 1024:.1f} MB)", end="", flush=True)
            except Exception as e:
                fname = futures[future]
                errors.append((fname, str(e)))
                print(f"\n  Error uploading {fname}: {e}")

    print()
    print(f"\nDone: {uploaded}/{total_files} files uploaded ({total_bytes / 1024 / 1024:.1f} MB)")
    if errors:
        print(f"{len(errors)} errors occurred:")
        for fname, err in errors:
            print(f"  {fname}: {err}")
        sys.exit(1)


if __name__ == "__main__":
    main()
