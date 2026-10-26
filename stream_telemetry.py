#!/usr/bin/env python3
"""
Mothbox Live Telemetry & Log Streamer
Streams real-time hardware metrics (INA219 voltage, current), capture events,
and scheduler logs directly from the Mothbox to your terminal.

Usage:
    python3 stream_telemetry.py
"""
import sys
import os
import time
import pexpect

CANDIDATES = [sys.argv[1]] if len(sys.argv) > 1 else [
    os.environ.get("MOTHBOX_HOST"),
    "192.168.50.5",   # AccessPopup AP default IP
    "mothbox.local",  # mDNS broadcast
    "10.42.0.94",     # Ethernet connection
]
CANDIDATE_HOSTS = [h for h in CANDIDATES if h]
HOST = CANDIDATE_HOSTS[0]
USER = "pi"
PASS = "luna"

# Terminal ANSI colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

import re

# Local session log mirroring
LOCAL_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(LOCAL_LOG_DIR, exist_ok=True)
SESSION_LOG_PATH = os.path.join(
    LOCAL_LOG_DIR, f"telemetry_{time.strftime('%Y%m%d_%H%M%S')}.log"
)
ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

def emit(text):
    print(text, flush=True)
    clean = ANSI_RE.sub('', text)
    try:
        with open(SESSION_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(clean + "\n")
    except Exception:
        pass

def log(msg, color=RESET):
    timestamp = time.strftime("%H:%M:%S")
    emit(f"{DIM}[{timestamp}]{RESET} {color}{msg}{RESET}")

def wait_for_connection():
    global HOST
    first_attempt = True
    while True:
        for candidate in CANDIDATE_HOSTS:
            try:
                child = pexpect.spawn(
                    f"ssh -F /dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=2 {USER}@{candidate} 'echo ready'",
                    timeout=4
                )
                idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=2)
                if idx == 0:
                    child.sendline(PASS)
                    child.expect(pexpect.EOF, timeout=3)
                    if b"ready" in child.before:
                        HOST = candidate
                        log(f"Connected to Mothbox SSH daemon @ {HOST}!", GREEN + BOLD)
                        return True
            except Exception:
                pass

        if first_attempt:
            log(f"Searching for Mothbox on {', '.join(CANDIDATE_HOSTS)} (sleeping/offline)...", YELLOW)
            first_attempt = False
        time.sleep(2)

def stream_feed():
    emit(f"\n{BOLD}{'=' * 80}")
    emit(f"      📡 MOTHBOX LIVE FIELD TELEMETRY FEED (webbedPavo @ {HOST})")
    emit(f"      Streaming TakePhoto & Scheduler logs + INA219 power telemetry")
    emit(f"      Saving local log copy to: {SESSION_LOG_PATH}")
    emit(f"      Press Ctrl+C to stop streaming")
    emit(f"{'=' * 80}{RESET}\n")

    while True:
        try:
            wait_for_connection()

            child = pexpect.spawn(
                f"ssh -F /dev/null -o StrictHostKeyChecking=no {USER}@{HOST} 'python3 -u /home/pi/Desktop/Mothbox/scripts/telemetry_daemon.py'",
                timeout=None
            )
            idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=5)
            if idx == 0:
                child.sendline(PASS)

            while True:
                line = child.readline().decode(errors="replace").strip()
                if not line:
                    continue

                ts = time.strftime("%H:%M:%S")

                if line.startswith("__MB_TEL__:"):
                    parts = line.split(":")
                    if len(parts) >= 5:
                        v = parts[1]
                        a = parts[2]
                        n_photos = parts[3]
                        mode = parts[4]
                        p_tag = parts[5] if len(parts) >= 6 else "IDLE"
                        thr_hex = parts[6] if len(parts) >= 7 else ""
                        # Determine battery health color
                        try:
                            vf = float(v)
                            pwr_col = GREEN if vf >= 11.5 else (YELLOW if vf >= 10.5 else RED)
                        except Exception:
                            pwr_col = CYAN

                        thr_seg = ""
                        if thr_hex:
                            thr_col = GREEN if thr_hex == "0x0" else (YELLOW if thr_hex == "n/a" else RED + BOLD)
                            thr_seg = f"  |  {BOLD}[PMIC]{RESET} {thr_col}throttled={thr_hex}{RESET}"

                        tag_label = f"[{p_tag} POWER]" if p_tag == "IDLE" else f"{YELLOW}[FLASH ACTIVE]{RESET}"
                        emit(f"{DIM}[{ts}]{RESET} {BOLD}{pwr_col}{tag_label}{RESET} {v}V / {a}A  |  {BOLD}[PHOTOS]{RESET} {n_photos} saved  |  {BOLD}[MODE]{RESET} {mode}{thr_seg}")

                elif line.startswith("__MB_TP__"):
                    msg = line[9:]
                    if "sag -" in msg:
                        emit(f"{DIM}[{ts}]{RESET} {YELLOW}{BOLD}⚡ [FLASH SAG]{RESET} {BOLD}{msg}{RESET}")
                    elif "flash" in msg.lower() or "burst" in msg.lower():
                        col = CYAN
                        emit(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}")
                    elif "saved" in msg.lower() or "complete" in msg.lower():
                        col = GREEN + BOLD
                        emit(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}")
                    elif "error" in msg.lower() or "fail" in msg.lower() or "abort" in msg.lower():
                        col = RED + BOLD
                        emit(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}")
                    else:
                        col = RESET
                        emit(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}")

                elif line.startswith("__MB_SCH__"):
                    msg = line[10:]
                    if "wake" in msg.lower() or "shutdown" in msg.lower() or "sleep" in msg.lower():
                        col = YELLOW + BOLD
                    elif "backup" in msg.lower():
                        col = GREEN
                    elif "error" in msg.lower() or "exception" in msg.lower() or "traceback" in msg.lower():
                        col = RED + BOLD
                    else:
                        col = DIM
                    emit(f"{DIM}[{ts}]{RESET} {col}⏱️  [Scheduler] {msg}{RESET}")

        except KeyboardInterrupt:
            log("\nStreaming stopped by user.", YELLOW)
            break
        except Exception as e:
            log(f"Connection dropped ({e}). Reconnecting in 3s...", RED)
            time.sleep(3)

if __name__ == "__main__":
    stream_feed()
