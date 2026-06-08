#!/usr/bin/env python3
"""
batch_scan.py — Run live_media_scan_producer on every WAV file in a directory,
processing multiple files in parallel, and writing results to a CSV file.

Usage:
    python batch_scan.py <audio-dir> [output.csv] [options]

Examples:
    python batch_scan.py ./samples
    python batch_scan.py ./samples results.csv --max-parallel 10
    python batch_scan.py ./samples results.csv --real-call
"""
import argparse
import csv
import os
import re
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

_print_lock = threading.Lock()


def log(msg: str) -> None:
    with _print_lock:
        print(msg, flush=True)


def run_scan(wav_file: Path, script_dir: Path, test: bool, debug: bool) -> dict:
    """Run the producer on a single WAV file and return a result dict."""
    cmd = [
        "uv", "run", "python", "src/live_media_scan_producer",
        "--file", str(wav_file),
    ]
    if not test:
        cmd.append("--no-test")
    if debug:
        cmd.append("--debug")

    log(f"  Starting:  {wav_file.name}")

    result = {
        "filename": wav_file.name,
        "session_id": "",
        "stream_id": "",
        "conclusion": "",
        "probability": "",
        "error": "",
    }

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=script_dir,
        )
        # logging writes to stderr; merge both streams for parsing
        output = proc.stdout + proc.stderr

        match = re.search(
            r"Session results: session_id=(\S+), stream_id=(\S+), conclusion=(\S+), probability=(\S+)",
            output,
        )
        if match:
            result["session_id"]  = match.group(1).rstrip(",")
            result["stream_id"]   = match.group(2).rstrip(",")
            result["conclusion"]  = match.group(3).rstrip(",")
            result["probability"] = match.group(4)
            status = f"{result['conclusion']}  (probability={result['probability']})"
        elif proc.returncode != 0:
            lines = [l for l in output.splitlines() if l.strip()]
            result["error"] = lines[-1] if lines else "unknown error"
            status = f"ERROR: {result['error']}"
            log(f"--- output for {wav_file.name} ---\n{output.rstrip()}\n--- end output ---")
        else:
            result["error"] = "no session results returned"
            status = "WARNING: no session results returned"
            log(f"--- output for {wav_file.name} ---\n{output.rstrip()}\n--- end output ---")

    except Exception as e:
        result["error"] = str(e)
        status = f"ERROR: {e}"

    log(f"  Completed: {wav_file.name}: {status}")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Batch scan WAV files using live_media_scan_producer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("audio_dir", help="Directory containing WAV files")
    parser.add_argument(
        "output_csv", nargs="?", default="results.csv",
        help="Output CSV file (default: results.csv)",
    )
    parser.add_argument(
        "--max-parallel", "-n", type=int, default=20, metavar="N",
        help="Maximum number of concurrent scans (default: 20)",
    )
    parser.add_argument(
        "--test", action=argparse.BooleanOptionalAction, default=True,
        help="Mark sessions as test calls, use --no-test for real calls (default: --test)",
    )
    parser.add_argument(
        "--debug", "-d", action="store_true",
        help="Enable debug logging in each producer invocation",
    )
    args = parser.parse_args()

    audio_dir = Path(args.audio_dir).resolve()
    if not audio_dir.is_dir():
        print(f"ERROR: directory not found: {audio_dir}", file=sys.stderr)
        sys.exit(1)

    wav_files = sorted(audio_dir.glob("*.wav"))
    if not wav_files:
        print(f"No WAV files found in {audio_dir}", file=sys.stderr)
        sys.exit(1)

    script_dir = Path(__file__).parent.resolve()
    call_type = "TEST CALL" if args.test else "REAL CALL"
    workers = min(args.max_parallel, len(wav_files))

    print(f"Found {len(wav_files)} WAV file(s) — running {workers} in parallel [{call_type}]")
    print(f"Output: {args.output_csv}")

    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(run_scan, f, script_dir, args.test, args.debug): f
            for f in wav_files
        }
        for future in as_completed(futures):
            results.append(future.result())

    results.sort(key=lambda r: r["filename"])

    fieldnames = ["filename", "session_id", "stream_id", "conclusion", "probability", "error"]
    with open(args.output_csv, "w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)

    processed = sum(1 for r in results if r["conclusion"])
    errors    = sum(1 for r in results if r["error"])
    print(f"\nDone: {processed} processed, {errors} errors — results written to {args.output_csv}")


if __name__ == "__main__":
    main()
