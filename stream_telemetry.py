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

HOST = "10.42.0.94"
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

def log(msg, color=RESET):
    timestamp = time.strftime("%H:%M:%S")
    print(f"{DIM}[{timestamp}]{RESET} {color}{msg}{RESET}", flush=True)

def wait_for_connection():
    log("Connecting to Mothbox...", CYAN)
    while True:
        try:
            # Check ethernet carrier first
            carrier_up = False
            try:
                with open("/sys/class/net/eno1/carrier", "r") as f:
                    carrier_up = (f.read().strip() == "1")
            except Exception:
                carrier_up = True

            if not carrier_up:
                log("Mothbox ethernet link is down (box is sleeping/powered off). Waiting...", YELLOW)
                time.sleep(2)
                continue

            # Attempt SSH
            child = pexpect.spawn(
                f"ssh -F /dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=3 {USER}@{HOST} 'echo ready'",
                timeout=5
            )
            idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=3)
            if idx == 0:
                child.sendline(PASS)
                child.expect(pexpect.EOF, timeout=5)
                if b"ready" in child.before:
                    log("Connected to Mothbox SSH daemon!", GREEN + BOLD)
                    return True
        except Exception:
            pass
        time.sleep(2)

def stream_feed():
    print(f"\n{BOLD}{'=' * 80}")
    print(f"      📡 MOTHBOX LIVE FIELD TELEMETRY FEED (webbedPavo @ {HOST})")
    print(f"      Streaming TakePhoto & Scheduler logs + INA219 power telemetry")
    print(f"      Press Ctrl+C to stop streaming")
    print(f"{'=' * 80}{RESET}\n", flush=True)

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
                        # Determine battery health color
                        try:
                            vf = float(v)
                            pwr_col = GREEN if vf >= 11.5 else (YELLOW if vf >= 10.5 else RED)
                        except Exception:
                            pwr_col = CYAN

                        tag_label = f"[{p_tag} POWER]" if p_tag == "IDLE" else f"{YELLOW}[FLASH ACTIVE]{RESET}"
                        print(f"{DIM}[{ts}]{RESET} {BOLD}{pwr_col}{tag_label}{RESET} {v}V / {a}A  |  {BOLD}[PHOTOS]{RESET} {n_photos} saved  |  {BOLD}[MODE]{RESET} {mode}", flush=True)

                elif line.startswith("__MB_TP__"):
                    msg = line[9:]
                    if "sag -" in msg:
                        print(f"{DIM}[{ts}]{RESET} {YELLOW}{BOLD}⚡ [FLASH SAG]{RESET} {BOLD}{msg}{RESET}", flush=True)
                    elif "flash" in msg.lower() or "burst" in msg.lower():
                        col = CYAN
                        print(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}", flush=True)
                    elif "saved" in msg.lower() or "complete" in msg.lower():
                        col = GREEN + BOLD
                        print(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}", flush=True)
                    elif "error" in msg.lower() or "fail" in msg.lower() or "abort" in msg.lower():
                        col = RED + BOLD
                        print(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}", flush=True)
                    else:
                        col = RESET
                        print(f"{DIM}[{ts}]{RESET} {col}📸 [TakePhoto] {msg}{RESET}", flush=True)

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
                    print(f"{DIM}[{ts}]{RESET} {col}⏱️  [Scheduler] {msg}{RESET}", flush=True)

        except KeyboardInterrupt:
            log("\nStreaming stopped by user.", YELLOW)
            break
        except Exception as e:
            log(f"Connection dropped ({e}). Reconnecting in 3s...", RED)
            time.sleep(3)

if __name__ == "__main__":
    stream_feed()
