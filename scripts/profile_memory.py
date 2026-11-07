"""Sample macOS process footprints without collecting camera frames or heap contents."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import time


UNITS = {"B": 1 / 1024**2, "K": 1 / 1024, "M": 1, "G": 1024, "T": 1024**2}


def size_mib(value):
    match = re.fullmatch(r"([\d.]+)([BKMGT])", value)
    if not match:
        raise ValueError("Unknown vmmap size: " + value)
    return float(match[1]) * UNITS[match[2]]


def parse_summary(raw):
    values = {}
    for field, label in (("footprint_mib", "Physical footprint"),
                         ("peak_mib", "Physical footprint (peak)")):
        match = re.search(r"^" + re.escape(label) + r":\s+([\d.]+[BKMGT])", raw, re.MULTILINE)
        if match:
            values[field] = size_mib(match[1])
    for label in ("WebKit Malloc", "JS JIT Generated Code", "Image IO", "IOSurface"):
        match = re.search(r"^" + re.escape(label) + r"\s+\S+\s+(\S+)\s+(\S+)", raw, re.MULTILINE)
        if match:
            values[label] = {"resident_mib": size_mib(match[1]), "dirty_mib": size_mib(match[2])}
    if "footprint_mib" not in values:
        raise ValueError("vmmap returned no physical footprint; process exited or access was denied")
    return values


def sample(pid):
    result = subprocess.run(["/usr/bin/vmmap", "-summary", str(pid)],
                            text=True, capture_output=True, timeout=15, check=True)
    identity = re.search(r"^Launch Time:\s+(.+)$", result.stdout, re.MULTILINE)
    if not identity:
        raise ValueError("vmmap returned no process launch time")
    values = parse_summary(result.stdout)
    values["launch_time"] = identity[1]
    return identity[1], values, result.stdout


def positive(value):
    number = float(value)
    if number <= 0 or not number < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and greater than zero")
    return number


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, action="append", help="Repeat for WebContent, Orca, and GPU processes")
    parser.add_argument("--duration", type=positive, default=600, help="Seconds to record (default: 600)")
    parser.add_argument("--interval", type=positive, default=10, help="Seconds between samples (default: 10)")
    parser.add_argument("--output", type=Path, required=True, help="New JSONL file; existing files are never replaced")
    parser.add_argument("--phase-file", type=Path, help="Read a plain label each sample, such as playing, stopped, or away")
    parser.add_argument("--save-summaries", action="store_true", help="Keep vmmap summaries beside the recording")
    args = parser.parse_args()
    if any(pid <= 0 for pid in args.pid):
        parser.error("process IDs must be greater than zero")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    identities = {}
    started = time.monotonic()
    summaries = args.output.with_suffix(".summaries")
    if args.save_summaries:
        summaries.mkdir(exist_ok=False)
    with args.output.open("x") as output:
        while True:
            sampled = time.monotonic()
            phase = args.phase_file.read_text().strip() if args.phase_file else "unlabelled"
            row = {"utc": datetime.now(timezone.utc).isoformat(),
                   "seconds": round(sampled - started, 2), "phase": phase, "processes": {}}
            for pid in args.pid:
                try:
                    identity, values, raw = sample(pid)
                    if pid in identities and identities[pid] != identity:
                        raise ValueError("PID was reused; start a new recording")
                    identities[pid] = identity
                    row["processes"][str(pid)] = values
                    if args.save_summaries:
                        (summaries / f"{row['seconds']:010.2f}-{pid}.txt").write_text(raw)
                except (subprocess.SubprocessError, ValueError) as error:
                    row["processes"][str(pid)] = {"error": str(error)}
                    output.write(json.dumps(row) + "\n")
                    output.flush()
                    raise SystemExit(f"Cannot sample PID {pid}; recording stopped") from error
            output.write(json.dumps(row) + "\n")
            output.flush()
            print(json.dumps(row), flush=True)
            if time.monotonic() - started >= args.duration:
                break
            time.sleep(max(0, min(args.interval - (time.monotonic() - sampled),
                                  args.duration - (time.monotonic() - started))))


if __name__ == "__main__":
    main()
