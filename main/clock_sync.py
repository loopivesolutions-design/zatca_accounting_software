"""
Startup guard against large clock drift (ZATCA signing / timestamps).

Uses a single SNTP request to pool.ntp.org. Override with SKIP_CLOCK_DRIFT_CHECK=1
for air-gapped or container environments where NTP is unavailable at process start.
"""

from __future__ import annotations

import os
import socket
import struct
import time


def _ntp_unix_time(addr: tuple[str, int] = ("pool.ntp.org", 123), *, timeout: float = 5.0) -> float:
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(timeout)
    try:
        client.sendto(b"\x1b" + 47 * b"\0", addr)
        data, _ = client.recvfrom(128)
    finally:
        client.close()
    if len(data) < 48:
        raise OSError("short NTP response")
    seconds, frac = struct.unpack("!II", data[40:48])
    return seconds - 2208988800 + frac / 2**32


def enforce_clock_drift_limit_on_startup() -> None:
    if str(os.getenv("SKIP_CLOCK_DRIFT_CHECK", "")).strip().lower() in {"1", "true", "yes", "on"}:
        return

    from django.conf import settings

    if settings.DEBUG and str(os.getenv("ENFORCE_CLOCK_DRIFT_IN_DEBUG", "")).strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }:
        return

    max_drift = float(os.getenv("MAX_CLOCK_DRIFT_SECONDS", "2").strip() or "2")

    try:
        remote = _ntp_unix_time()
    except OSError:
        if str(os.getenv("ALLOW_STARTUP_WITHOUT_NTP", "")).strip().lower() in {"1", "true", "yes", "on"}:
            return
        raise SystemExit(
            "CRITICAL: Could not reach an NTP server to verify system time. "
            "Fix network/time sync, or set SKIP_CLOCK_DRIFT_CHECK=1 or ALLOW_STARTUP_WITHOUT_NTP=1 "
            "(document the exception for auditors)."
        ) from None

    local = time.time()
    if abs(local - remote) > max_drift:
        raise SystemExit(
            f"CRITICAL: Clock drift too high: |local - NTP| = {abs(local - remote):.3f}s "
            f"(limit {max_drift}s). Sync the host clock before ZATCA operations."
        )
