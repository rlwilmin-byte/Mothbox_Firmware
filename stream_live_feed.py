#!/usr/bin/env python3
"""
Stream live Mothbox capture events and logs directly to your terminal.
Usage: python3 stream_live_feed.py
"""
import sys
import os
import pexpect

CANDIDATES = [sys.argv[1]] if len(sys.argv) > 1 else [
    os.environ.get("MOTHBOX_HOST"),
    "192.168.50.5",
    "mothbox.local",
    "10.42.0.94",
]
CANDIDATE_HOSTS = [h for h in CANDIDATES if h]
USER = "pi"
PASS = "luna"

def main():
    selected_host = CANDIDATE_HOSTS[0]
    for candidate in CANDIDATE_HOSTS:
        try:
            test = pexpect.spawn(f"ssh -F /dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=2 {USER}@{candidate} 'echo ok'", timeout=4)
            idx = test.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=2)
            if idx == 0:
                test.sendline(PASS)
                test.expect(pexpect.EOF, timeout=3)
                if b"ok" in test.before:
                    selected_host = candidate
                    break
        except Exception:
            pass

    # Local session log mirroring
    local_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
    os.makedirs(local_log_dir, exist_ok=True)
    session_log = os.path.join(local_log_dir, f"live_feed_{time.strftime('%Y%m%d_%H%M%S')}.log")

    print(f"Connecting to Mothbox live stream ({selected_host})...")
    print(f"Saving local log copy to: {session_log}")
    child = pexpect.spawn(
        f"ssh -F /dev/null -o StrictHostKeyChecking=no {USER}@{selected_host} 'tail -f -n 20 /home/pi/Desktop/Mothbox/logs/TakePhoto_log.txt'",
        timeout=None
    )
    try:
        idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=10)
        if idx == 0:
            child.sendline(PASS)
            print("\n" + "=" * 70)
            print("STREAMING LIVE TAKEPHOTO LOG (Press Ctrl+C to stop)")
            print("=" * 70 + "\n")
            with open(session_log, "a", encoding="utf-8") as f:
                while True:
                    line = child.readline().decode(errors="replace")
                    if not line:
                        break
                    print(line, end="", flush=True)
                    f.write(line)
                    f.flush()
        else:
            print("Failed to authenticate.")
    except KeyboardInterrupt:
        print("\nStream stopped.")
    except Exception as e:
        print(f"Stream error: {e}")

if __name__ == "__main__":
    main()
