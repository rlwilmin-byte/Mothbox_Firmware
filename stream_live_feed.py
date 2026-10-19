#!/usr/bin/env python3
"""
Stream live Mothbox capture events and logs directly to your terminal.
Usage: python3 stream_live_feed.py
"""
import sys
import pexpect

HOST = "10.42.0.94"
USER = "pi"
PASS = "luna"

def main():
    print("Connecting to Mothbox live stream (10.42.0.94)...")
    child = pexpect.spawn(
        f"ssh -o StrictHostKeyChecking=no {USER}@{HOST} 'tail -f -n 20 /home/pi/Desktop/Mothbox/logs/TakePhoto_log.txt'",
        timeout=None
    )
    try:
        idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=10)
        if idx == 0:
            child.sendline(PASS)
            print("\n" + "=" * 70)
            print("STREAMING LIVE TAKEPHOTO LOG (Press Ctrl+C to stop)")
            print("=" * 70 + "\n")
            child.interact()
        else:
            print("Failed to authenticate.")
    except KeyboardInterrupt:
        print("\nStream stopped.")
    except Exception as e:
        print(f"Stream error: {e}")

if __name__ == "__main__":
    main()
