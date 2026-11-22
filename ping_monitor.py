#!/usr/bin/env python3
"""
Ping Monitor - checks if websites or devices are up, and logs downtime.

Uses only Python's standard library. Nothing to install.

How to run:
    python ping_monitor.py                  (uses hosts.txt, checks every 60 seconds)
    python ping_monitor.py -i 30            (check every 30 seconds)
    python ping_monitor.py google.com 192.168.1.1 https://example.com
    python ping_monitor.py --once           (check one time and exit)

Two kinds of targets:
    google.com, 192.168.1.1          -> checked with ping
    https://example.com              -> checked by loading the web page (HTTP)

Press Ctrl+C to stop. A summary is printed when you stop.
"""

import argparse
import csv
import os
import platform
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"


# ---------------------------------------------------------------- checks
def ping_host(host, timeout_s=2):
    """Send one ping. Returns (is_up, response_ms or None)."""
    if IS_WINDOWS:
        cmd = ["ping", "-n", "1", "-w", str(timeout_s * 1000), host]
    elif IS_MAC:
        cmd = ["ping", "-c", "1", "-W", str(timeout_s * 1000), host]
    else:
        cmd = ["ping", "-c", "1", "-W", str(timeout_s), host]

    start = time.perf_counter()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True,
                                timeout=timeout_s + 3)
    except FileNotFoundError:
        sys.exit("Error: the 'ping' command was not found on this computer.")
    except subprocess.TimeoutExpired:
        return False, None
    elapsed_ms = (time.perf_counter() - start) * 1000

    if result.returncode != 0:
        return False, None
    # On Windows, "Destination host unreachable" can still return code 0.
    # A real reply always contains "TTL=".
    if IS_WINDOWS and "TTL=" not in result.stdout.upper():
        return False, None
    return True, round(elapsed_ms)


def http_check(url, timeout_s=5):
    """Load a web page. Up = server answered with a status below 500."""
    start = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": "PingMonitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            code = resp.status
    except urllib.error.HTTPError as e:
        code = e.code                    # server answered, but with an error code
    except Exception:
        return False, None               # no answer at all (DNS, timeout, refused)
    elapsed_ms = round((time.perf_counter() - start) * 1000)
    return code < 500, elapsed_ms


def check(target):
    if target.startswith(("http://", "https://")):
        return http_check(target)
    return ping_host(target)


# ---------------------------------------------------------------- helpers
def load_targets(path):
    """Read targets from a text file. One per line. Lines starting with # are skipped."""
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [line.strip() for line in f
                if line.strip() and not line.strip().startswith("#")]


def fmt_duration(seconds):
    seconds = int(round(seconds))
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h {m}m {s}s"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


def now_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def write_row(path, row):
    new_file = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time", "target", "status", "response_ms", "event"])
        w.writerow(row)


# ---------------------------------------------------------------- main loop
def main():
    parser = argparse.ArgumentParser(description="Check if hosts are up and log downtime.")
    parser.add_argument("targets", nargs="*", help="hosts or URLs (default: read hosts.txt)")
    parser.add_argument("-f", "--file", default="hosts.txt", help="file with targets (default: hosts.txt)")
    parser.add_argument("-i", "--interval", type=int, default=60, help="seconds between checks (default: 60)")
    parser.add_argument("-l", "--log", default="ping_log.csv", help="log file (default: ping_log.csv)")
    parser.add_argument("--once", action="store_true", help="check one time, then exit")
    args = parser.parse_args()

    targets = args.targets or load_targets(args.file)
    if not targets:
        sys.exit(f"No targets. Add some to {args.file}, or run: python ping_monitor.py google.com")
    if args.interval < 5:
        sys.exit("Interval must be at least 5 seconds (to avoid flooding the network).")

    # State per target
    state = {t: {"up": None, "down_since": None, "checks": 0, "ups": 0,
                 "outages": 0, "downtime": 0.0} for t in targets}

    print(f"Monitoring {len(targets)} target(s) every {args.interval}s. Log: {args.log}")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            for t in targets:
                up, ms = check(t)
                s = state[t]
                s["checks"] += 1
                stamp = now_str()
                event = ""

                if up:
                    s["ups"] += 1
                    if s["up"] is False:                       # was down, now back
                        dur = time.time() - s["down_since"]
                        s["downtime"] += dur
                        s["down_since"] = None
                        event = f"RECOVERED after {fmt_duration(dur)}"
                else:
                    if s["up"] is not False:                   # just went down
                        s["down_since"] = time.time()
                        s["outages"] += 1
                        event = "WENT DOWN"
                s["up"] = up

                status = "UP" if up else "DOWN"
                ms_txt = f"{ms} ms" if ms is not None else "-"
                line = f"[{stamp}] {status:4}  {t:30} {ms_txt:>8}"
                if event:
                    line += f"   <-- {event}"
                print(line)
                write_row(args.log, [stamp, t, status, ms if ms is not None else "", event])

            if args.once:
                break
            print()
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")

    # Summary
    print("\n===== Summary =====")
    for t, s in state.items():
        downtime = s["downtime"]
        if s["down_since"] is not None:                       # still down at the end
            downtime += time.time() - s["down_since"]
        uptime_pct = (s["ups"] / s["checks"] * 100) if s["checks"] else 0
        print(f"{t}: uptime {uptime_pct:.1f}% of {s['checks']} checks, "
              f"{s['outages']} outage(s), total downtime {fmt_duration(downtime)}")


if __name__ == "__main__":
    main()
