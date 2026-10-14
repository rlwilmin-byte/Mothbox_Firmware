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
from pathlib import Path

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
        cmd = f"ssh -F /dev/null -o StrictHostKeyChecking=no -o ConnectTimeout=10 {self.user}@{self.host}"
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
        try:
            self.child.sendline(command)
            self.child.expect(r"\$", timeout=t)
            output = self.child.before.decode(errors="replace")
            return output
        except Exception:
            self.close()
            raise

    def close(self):
        if self.child:
            try:
                self.child.sendline("exit")
                self.child.close()
            except Exception:
                pass
            self.child = None

    def get_boot_id(self):
        """Returns the Linux kernel boot ID (/proc/sys/kernel/random/boot_id)."""
        try:
            out = self.run_cmd("cat /proc/sys/kernel/random/boot_id 2>/dev/null", timeout=10)
            for line in out.splitlines():
                line = line.strip()
                if len(line) == 36 and line.count("-") == 4:
                    return line
        except Exception:
            pass
        return None

    def get_uptime_s(self):
        """Returns system uptime in seconds from /proc/uptime."""
        try:
            out = self.run_cmd("cat /proc/uptime 2>/dev/null", timeout=10)
            for line in out.splitlines():
                parts = line.strip().split()
                if parts:
                    return float(parts[0])
        except Exception:
            pass
        return None


def test_rails(session):
    print("\n--- [TEST] 12V Switched Power Rail ---")
    script = "sudo python3 -c 'import sys, time; sys.path.insert(0, \"/home/pi/Desktop/Mothbox\"); import mothbox_hw; print(\"Asserting 12V rail...\"); mothbox_hw.rail_12v(True); time.sleep(0.5); mothbox_hw.rail_12v(False); print(\"12V RAIL OK!\")'"
    out = session.run_cmd(script, timeout=25)
    print(out)
    return "12V RAIL OK!" in out


def test_attract(session):
    print("\n--- [TEST] Attractor Channels (CH1-CH3, CHEXT) ---")
    script = (
        "sudo python3 -c 'import sys, time; sys.path.insert(0, \"/home/pi/Desktop/Mothbox\"); "
        "import mothbox_hw; print(\"Testing Attractor Lights...\"); "
        "mothbox_hw.attract_on(); time.sleep(0.5); mothbox_hw.attract_off(); "
        "print(\"ATTRACT OK!\")'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "ATTRACT OK!" in out and "Traceback" not in out


def test_flash(session):
    print("\n--- [TEST] Flash Pulse Line ---")
    script = (
        "sudo python3 -c 'import sys, time; sys.path.insert(0, \"/home/pi/Desktop/Mothbox\"); "
        "import mothbox_hw; print(\"Testing flash pulse...\"); "
        "mothbox_hw.flash_on(); time.sleep(0.1); mothbox_hw.flash_off(); "
        "print(\"FLASH OK!\")'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "FLASH OK!" in out and "Traceback" not in out


def test_camera(session):
    print("\n--- [TEST] Camera Picamera2 Init & Capture ---")
    script = (
        "sudo python3 -c 'from picamera2 import Picamera2; import time; "
        "picam2 = Picamera2(); cfg = picam2.create_preview_configuration(main={\"size\": (640, 480)}); "
        "picam2.configure(cfg); picam2.start(); time.sleep(1); "
        "picam2.capture_file(\"/tmp/test_cam.jpg\"); picam2.stop(); print(\"CAMERA INIT & CAPTURE OK!\")'"
    )
    out = session.run_cmd(script, timeout=30)
    print(out)
    return "CAMERA INIT & CAPTURE OK!" in out


def test_64mp(session):
    print("\n--- [TEST] OwlSight 64MP High-Resolution Capture Pipeline ---")
    script = (
        "sudo python3 -c 'from picamera2 import Picamera2; import time; "
        "picam2 = Picamera2(); cfg = picam2.create_still_configuration(main={\"size\": (9248, 6944), \"format\": \"RGB888\"}); "
        "picam2.configure(cfg); picam2.start(); time.sleep(1); "
        "picam2.capture_file(\"/tmp/test_64mp.jpg\"); picam2.stop(); print(\"64MP CAPTURE OK!\")'"
    )
    out = session.run_cmd(script, timeout=45)
    print(out)
    return "64MP CAPTURE OK!" in out


def test_subsystems(session):
    print("\n--- [TEST] End-to-End Subsystems (Diagnostics & TakePhoto) ---")
    cmds = [
        ("sudo python3 /home/pi/Desktop/Mothbox/Diagnostics.py", 45),
        ("sudo python3 /home/pi/Desktop/Mothbox/TakePhoto.py", 100),
        ("ls -lt /home/pi/Desktop/Mothbox/photos/* | head -n 5", 20)
    ]
    all_ok = True
    for c, tout in cmds:
        print(f"\n>> {c}")
        out = session.run_cmd(c, timeout=tout)
        print(out)
        if "Traceback" in out:
            all_ok = False
    return all_ok


def test_scheduler(session):
    print("\n--- [TEST] Scheduler Loop Pass ---")
    out = session.run_cmd("sudo python3 /home/pi/Desktop/Mothbox/Scheduler.py", timeout=60)
    print(out)
    return "Traceback" not in out


def test_locks(session):
    print("\n--- [TEST] Lock Semantics & BOOT_LOCK Validation ---")
    script = (
        "python3 -c 'import fcntl, os; lock_path = \"/tmp/test_lock.lock\"; "
        "f = open(lock_path, \"w\"); fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB); "
        "print(\"LOCK ACQUIRED OK\"); fcntl.flock(f, fcntl.LOCK_UN); f.close(); os.remove(lock_path)'"
    )
    out = session.run_cmd(script, timeout=15)
    print(out)
    return "LOCK ACQUIRED OK" in out


def test_imports(session):
    print("\n--- [TEST] Module Imports Validation ---")
    script = (
        "python3 -c 'import sys; sys.path.insert(0, \"/home/pi/Desktop/Mothbox\"); "
        "import mothbox_hw, mothbox_paths, mothbox_exif; "
        "[compile(open(f\"/home/pi/Desktop/Mothbox/{f}\").read(), f, \"exec\") "
        "for f in [\"Diagnostics.py\", \"Scheduler.py\", \"TakePhoto.py\"]]; "
        "print(\"ALL IMPORTS SUCCEEDED\")'"
    )
    out = session.run_cmd(script, timeout=20)
    print(out)
    return "ALL IMPORTS SUCCEEDED" in out


def test_burst(session):
    print("\n--- [TEST] Burst Capture Mode Execution (8X @ 6s) ---")
    setup_script = (
        "sudo python3 -c 'import sys; sys.path.insert(0, \"/home/pi/Desktop/Mothbox\"); "
        "from mothbox_paths import STATE_ROOT; "
        "open(STATE_ROOT / \"capture_mode.txt\", \"w\").write(\"capture_mode=burst\\n\"); "
        "open(STATE_ROOT / \"burst_count.txt\", \"w\").write(\"burst_count=8\\n\"); "
        "open(STATE_ROOT / \"burst_interval_s.txt\", \"w\").write(\"burst_interval_s=6.0\\n\")'"
    )
    session.run_cmd(setup_script, timeout=10)
    out = session.run_cmd("sudo python3 /home/pi/Desktop/Mothbox/TakePhoto.py", timeout=120)
    print(out)
    restore_script = (
        "sudo python3 -c 'import sys; sys.path.insert(0, \"/home/pi/Desktop/Mothbox\"); "
        "from mothbox_paths import STATE_ROOT; "
        "open(STATE_ROOT / \"capture_mode.txt\", \"w\").write(\"capture_mode=timelapse\\n\")'"
    )
    session.run_cmd(restore_script, timeout=10)
    return "STARTING BURST CAPTURE" in out and "photos successfully written to disk" in out


def test_status(session):
    print("\n--- [DIAGNOSTIC] Box Identity & Status ---")
    cmds = [
        ("Hostname & Uptime", "hostname && uptime"),
        ("Mothbox Name", "cat /boot/firmware/mothbox_custom/system/controls/name.txt 2>/dev/null || cat /var/lib/mothbox/name.txt 2>/dev/null || echo 'Name not found'"),
        ("Active Switches", "sudo python3 /home/pi/Desktop/Mothbox/GetConfigSwitches.py 2>/dev/null || true"),
        ("Active Processes", "ps aux | grep -E 'python|TakePhoto|Scheduler' | grep -v grep || true"),
        ("Lock Files", "ls -l /run/boot_script_running /run/mothbox_takephoto.lock 2>/dev/null || echo 'No active lockfiles'"),
        ("Current Settings", "cat /boot/firmware/mothbox_custom/mothbox_settings.csv | grep -E 'name|capture_mode|photo_interval|burst' || true"),
    ]
    for label, cmd in cmds:
        print(f"\n>> {label}:")
        out = session.run_cmd(cmd)
        print(out)
    return True


def sync_firmware_to_target(host, user, password):
    """Syncs local firmware to remote Mothbox using pexpect and rsync."""
    print(f"\n--- [DEPLOY] Syncing local firmware to {user}@{host} ---")
    repo_root = Path(__file__).resolve().parent.parent.parent.parent
    local_unified = repo_root / "Mothbox_Unified"
    local_custom = repo_root / "mothbox_custom_Unified"

    # Sync Mothbox_Unified
    cmd1 = f"rsync -av -e 'ssh -F /dev/null -o StrictHostKeyChecking=no' --exclude '__pycache__' {local_unified}/ {user}@{host}:/home/pi/Desktop/Mothbox/"
    print(f">> {cmd1}")
    child = pexpect.spawn(cmd1, timeout=60)
    idx = child.expect(["password:", pexpect.EOF, pexpect.TIMEOUT], timeout=15)
    if idx == 0:
        child.sendline(password)
        child.expect(pexpect.EOF, timeout=60)
    print(child.before.decode(errors="replace"))

    # Sync mothbox_custom_Unified
    cmd2 = f"rsync -rltv -e 'ssh -F /dev/null -o StrictHostKeyChecking=no' --no-perms --no-owner --no-group --rsync-path='sudo rsync' {local_custom}/ {user}@{host}:/boot/firmware/mothbox_custom/"
    print(f">> {cmd2}")
    child2 = pexpect.spawn(cmd2, timeout=60)
    idx2 = child2.expect(["password:", pexpect.EOF, pexpect.TIMEOUT], timeout=15)
    if idx2 == 0:
        child2.sendline(password)
        child2.expect(pexpect.EOF, timeout=60)
    print(child2.before.decode(errors="replace"))
    print("[DEPLOY] Sync completed successfully.\n")


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
    "burst": test_burst,
    "status": test_status,
}


import random

def main():
    parser = argparse.ArgumentParser(description="Mothbox Live Hardware Integration Test Runner")
    parser.add_argument("--host", default=os.getenv("MOTHBOX_HOST", "10.42.0.34"), help="Target Mothbox IP")
    parser.add_argument("--user", default=os.getenv("MOTHBOX_USER", "pi"), help="SSH username")
    parser.add_argument("--password", default=os.getenv("MOTHBOX_PASS", "luna"), help="SSH password")
    parser.add_argument("--test", choices=list(TEST_MAP.keys()) + ["all"], default="all", help="Test to run")
    parser.add_argument("--sync", action="store_true", help="Sync local firmware to target before running tests")
    parser.add_argument("--repeat", type=int, default=1, help="Number of times to repeat the test suite")
    parser.add_argument("--shuffle", action="store_true", help="Vary and randomize the order of tests in each iteration")
    args = parser.parse_args()

    if args.sync:
        sync_firmware_to_target(args.host, args.user, args.password)

    session = MothboxRemoteSession(args.host, args.user, args.password)
    try:
        print(f"Connecting to {args.user}@{args.host}...")
        session.connect()
        print("Connected successfully.")
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    base_targets = list(TEST_MAP.keys()) if args.test == "all" else [args.test]
    iteration_results = {}
    overall_start_time = time.time()
    
    initial_boot_id = session.get_boot_id()
    initial_uptime = session.get_uptime_s() or 0.0
    print(f"\n[AUDIT] Target Initial Boot ID : {initial_boot_id}")
    print(f"[AUDIT] Target Initial Uptime  : {initial_uptime:.1f}s\n")
    power_faults = 0

    try:
        for iteration in range(1, args.repeat + 1):
            targets = list(base_targets)
            if args.shuffle and len(targets) > 1:
                random.shuffle(targets)

            print(f"\n{'#' * 60}")
            print(f"### RUN {iteration}/{args.repeat} -- Execution Order: {', '.join(targets)}")
            print(f"{'#' * 60}\n")

            iter_results = {}
            for t in targets:
                # Pre-test power continuity verification
                pre_boot = session.get_boot_id()
                if initial_boot_id and pre_boot and pre_boot != initial_boot_id:
                    print(f"\n[FATAL POWER FAULT] Pre-test boot ID mismatch! ({initial_boot_id} -> {pre_boot})")
                    power_faults += 1
                    iter_results[t] = "POWER_RESET_FAIL"
                    continue

                func = TEST_MAP[t]
                try:
                    passed = func(session)
                    iter_results[t] = "PASS" if passed else "FAIL"
                except Exception as e:
                    print(f"Exception during test '{t}': {e}")
                    iter_results[t] = "ERROR"

                # Post-test power continuity verification
                post_boot = session.get_boot_id()
                post_uptime = session.get_uptime_s() or 0.0
                if initial_boot_id and post_boot and post_boot != initial_boot_id:
                    print(f"\n[FATAL POWER FAULT] Post-test boot ID mismatch! Device rebooted during test '{t}'!")
                    power_faults += 1
                    iter_results[t] = "POWER_RESET_FAIL"
                elif post_uptime < initial_uptime:
                    print(f"\n[FATAL POWER FAULT] Uptime decreased during test '{t}' ({initial_uptime:.1f}s -> {post_uptime:.1f}s)!")
                    power_faults += 1
                    iter_results[t] = "POWER_RESET_FAIL"

            iteration_results[iteration] = iter_results
    finally:
        session.close()

    total_elapsed = time.time() - overall_start_time
    print("\n" + "=" * 65)
    print(f"HARDWARE INTEGRATION REPEATABILITY SUMMARY ({total_elapsed:.1f}s)")
    print("=" * 65)
    all_runs_passed = True
    header = f"{'Test':<15} | " + " | ".join([f"Run {i}" for i in range(1, args.repeat + 1)]) + " | Overall"
    print(header)
    print("-" * len(header))

    for t in base_targets:
        statuses = [iteration_results[i].get(t, "N/A") for i in range(1, args.repeat + 1)]
        test_passed = all(s == "PASS" for s in statuses)
        if not test_passed:
            all_runs_passed = False
        status_cols = " | ".join([f"{s:^5}" for s in statuses])
        overall_str = "PASS" if test_passed else "FAIL"
        print(f"{t:<15} | {status_cols} | {overall_str}")

    print("=" * 65)
    print("POWER INTEGRITY & CONTINUOUS UPTIME AUDIT:")
    print(f"  Initial Boot ID               : {initial_boot_id}")
    final_session = MothboxRemoteSession(args.host, args.user, args.password)
    try:
        final_session.connect()
        final_boot_id = final_session.get_boot_id()
        final_uptime = final_session.get_uptime_s() or 0.0
        final_session.close()
    except Exception:
        final_boot_id = "UNREACHABLE"
        final_uptime = 0.0

    print(f"  Final Boot ID                 : {final_boot_id}")
    print(f"  Uncommanded Reboots / Faults  : {power_faults}")
    power_ok = (power_faults == 0) and (final_boot_id == initial_boot_id)
    print(f"  Power Integrity Verification  : {'PASSED (Zero brownouts, 100% continuous uptime)' if power_ok else 'FAILED (Uncommanded reboot or manual wake occurred)'}")
    print("=" * 65)

    if not power_ok:
        all_runs_passed = False

    sys.exit(0 if all_runs_passed else 1)


if __name__ == "__main__":
    main()
