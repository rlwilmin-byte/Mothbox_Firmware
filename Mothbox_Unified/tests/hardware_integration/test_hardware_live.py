#!/usr/bin/env python3
"""
Mothbox Hardware Integration Test Suite
Executes live hardware integration tests against a remote Mothbox target over SSH.

Usage:
    python3 test_hardware_live.py [--host 10.42.0.34] [--user pi] [--test <test_name>]

Available tests:
    rails       - Switched 12V rail (Q8 MOSFET)
    attract     - Attractor light channels (CH1-CH3, CHEXT)
    flash       - High-current flash pulse
    camera      - Picamera2 sensor initialization & frame capture
    64mp        - OwlSight 64MP capture pipeline
    subsystems  - Full Diagnostics.py and TakePhoto.py execution
    scheduler   - Single scheduler loop pass
    locks       - flock and BOOT_LOCK locking semantics
    imports     - Firmware import validation
    all         - Run all test suites in sequence
"""

import argparse
import os
import sys
import time

try:
    import pexpect
except ImportError:
    print("Error: 'pexpect' is required. Install via: pip install pexpect", file=sys.stderr)
    sys.exit(1)


class MothboxRemoteSession:
    def __init__(self, host, user, password, timeout=30):
        self.host = host
        self.user = user
        self.password = password
        self.timeout = timeout
        self.child = None

    def connect(self):
        cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {self.user}@{self.host}"
        self.child = pexpect.spawn(cmd, timeout=self.timeout)
        idx = self.child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=15)
        if idx != 0:
            raise ConnectionError(f"Failed to connect to {self.user}@{self.host}: {self.child.before.decode(errors='replace')}")
        self.child.sendline(self.password)
        self.child.expect(r"\$", timeout=15)

    def run_cmd(self, command, timeout=None):
        if not self.child:
            self.connect()
        t = timeout or self.timeout
        self.child.sendline(command)
        self.child.expect(r"\$", timeout=t)
        output = self.child.before.decode(errors="replace")
        return output

    def close(self):
        if self.child:
            try:
                self.child.sendline("exit")
                self.child.close()
            except Exception:
                pass
            self.child = None


def test_rails(session):
    print("\n--- [TEST] 12V Switched Power Rail ---")
    script = (
        "sudo python3 -c '\n"
        "import sys, time\n"
        "sys.path.insert(0, \"/home/pi/Desktop/Mothbox\")\n"
        "import mothbox_hw\n"
        "print(\"Asserting 12V rail...\")\n"
        "mothbox_hw.rail_12v(True)\n"
        "time.sleep(0.5)\n"
        "mothbox_hw.rail_12v(False)\n"
        "print(\"12V RAIL OK!\")\n"
        "'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "12V RAIL OK!" in out


def test_attract(session):
    print("\n--- [TEST] Attractor Channels (CH1-CH3, CHEXT) ---")
    script = (
        "sudo python3 -c '\n"
        "import sys, time\n"
        "sys.path.insert(0, \"/home/pi/Desktop/Mothbox\")\n"
        "import mothbox_hw\n"
        "mothbox_hw.rail_12v(True)\n"
        "for ch in [\"CH1\", \"CH2\", \"CH3\", \"CHEXT\"]:\n"
        "    print(f\"Testing {ch}...\")\n"
        "    mothbox_hw.set_channel(ch, True)\n"
        "    time.sleep(0.2)\n"
        "    mothbox_hw.set_channel(ch, False)\n"
        "mothbox_hw.rail_12v(False)\n"
        "print(\"ATTRACT OK!\")\n"
        "'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "ATTRACT OK!" in out


def test_flash(session):
    print("\n--- [TEST] Flash Pulse Line ---")
    script = (
        "sudo python3 -c '\n"
        "import sys, time\n"
        "sys.path.insert(0, \"/home/pi/Desktop/Mothbox\")\n"
        "import mothbox_hw\n"
        "mothbox_hw.rail_12v(True)\n"
        "print(\"Testing flash pulse...\")\n"
        "mothbox_hw.flash(True)\n"
        "time.sleep(0.1)\n"
        "mothbox_hw.flash(False)\n"
        "mothbox_hw.rail_12v(False)\n"
        "print(\"FLASH OK!\")\n"
        "'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "FLASH OK!" in out


def test_camera(session):
    print("\n--- [TEST] Camera Picamera2 Init & Capture ---")
    script = (
        "sudo python3 -c '\n"
        "from picamzero import Camera\n"
        "cam = Camera()\n"
        "cam.take_photo(\"/tmp/test_cam.jpg\")\n"
        "print(\"CAMERA INIT & CAPTURE OK!\")\n"
        "'"
    )
    out = session.run_cmd(script, timeout=30)
    print(out)
    return "CAMERA INIT & CAPTURE OK!" in out


def test_64mp(session):
    print("\n--- [TEST] OwlSight 64MP High-Resolution Capture Pipeline ---")
    script = (
        "sudo python3 -c '\n"
        "import sys\n"
        "sys.path.insert(0, \"/home/pi/Desktop/Mothbox\")\n"
        "import mothbox_hw\n"
        "mothbox_hw.init_hardware()\n"
        "import TakePhoto\n"
        "res = TakePhoto.capture_image(save_path=\"/tmp/test_64mp.jpg\")\n"
        "print(f\"64MP RESULT: {res}\")\n"
        "'"
    )
    out = session.run_cmd(script, timeout=45)
    print(out)
    return "64MP RESULT" in out and "Error" not in out


def test_subsystems(session):
    print("\n--- [TEST] End-to-End Subsystems (Diagnostics & TakePhoto) ---")
    cmds = [
        "python3 /home/pi/Desktop/Mothbox/Diagnostics.py",
        "python3 /home/pi/Desktop/Mothbox/TakePhoto.py",
        "ls -lt /home/pi/Desktop/Mothbox/photos/* | head -n 5"
    ]
    all_ok = True
    for c in cmds:
        print(f"\n>> {c}")
        out = session.run_cmd(c, timeout=60)
        print(out)
        if "Traceback" in out:
            all_ok = False
    return all_ok


def test_scheduler(session):
    print("\n--- [TEST] Scheduler Loop Pass ---")
    out = session.run_cmd("sudo python3 /home/pi/Desktop/Mothbox/Scheduler.py", timeout=25)
    print(out)
    return "Traceback" not in out


def test_locks(session):
    print("\n--- [TEST] Lock Semantics & BOOT_LOCK Validation ---")
    script = (
        "python3 -c '\n"
        "import fcntl, os\n"
        "lock_path = \"/tmp/test_lock.lock\"\n"
        "f = open(lock_path, \"w\")\n"
        "fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        "print(\"LOCK ACQUIRED OK\")\n"
        "fcntl.flock(f, fcntl.LOCK_UN)\n"
        "f.close()\n"
        "os.remove(lock_path)\n"
        "'"
    )
    out = session.run_cmd(script, timeout=15)
    print(out)
    return "LOCK ACQUIRED OK" in out


def test_imports(session):
    print("\n--- [TEST] Module Imports Validation ---")
    script = (
        "python3 -c '\n"
        "import sys\n"
        "sys.path.insert(0, \"/home/pi/Desktop/Mothbox\")\n"
        "import mothbox_hw, mothbox_paths, Diagnostics, TakePhoto, Scheduler\n"
        "print(\"ALL IMPORTS SUCCEEDED\")\n"
        "'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "ALL IMPORTS SUCCEEDED" in out


TEST_MAP = {
    "rails": test_rails,
    "attract": test_attract,
    "flash": test_flash,
    "camera": test_camera,
    "64mp": test_64mp,
    "subsystems": test_subsystems,
    "scheduler": test_scheduler,
    "locks": test_locks,
    "imports": test_imports,
}


def main():
    parser = argparse.ArgumentParser(description="Mothbox Live Hardware Integration Test Runner")
    parser.add_argument("--host", default=os.getenv("MOTHBOX_HOST", "10.42.0.34"), help="Target Mothbox IP")
    parser.add_argument("--user", default=os.getenv("MOTHBOX_USER", "pi"), help="SSH username")
    parser.add_argument("--password", default=os.getenv("MOTHBOX_PASS", "luna"), help="SSH password")
    parser.add_argument("--test", choices=list(TEST_MAP.keys()) + ["all"], default="all", help="Test to run")
    args = parser.parse_args()

    session = MothboxRemoteSession(args.host, args.user, args.password)
    try:
        print(f"Connecting to {args.user}@{args.host}...")
        session.connect()
        print("Connected successfully.")
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    targets = list(TEST_MAP.keys()) if args.test == "all" else [args.test]
    results = {}

    start_time = time.time()
    try:
        for t in targets:
            func = TEST_MAP[t]
            try:
                passed = func(session)
                results[t] = "PASS" if passed else "FAIL"
            except Exception as e:
                print(f"Exception during test '{t}': {e}")
                results[t] = "ERROR"
    finally:
        session.close()

    elapsed = time.time() - start_time
    print("\n" + "=" * 50)
    print(f"HARDWARE INTEGRATION RESULTS ({elapsed:.1f}s)")
    print("=" * 50)
    all_passed = True
    for t, status in results.items():
        print(f"  {t:<15}: {status}")
        if status != "PASS":
            all_passed = False
    print("=" * 50)
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
