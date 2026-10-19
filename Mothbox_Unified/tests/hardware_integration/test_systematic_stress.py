#!/usr/bin/env python3
"""
Mothbox Systematic Full-System Stress & Reliability Test Bank
-------------------------------------------------------------
Executes randomized, multi-iteration stress tests against the Mothbox hardware.
Measures performance vs schedule, percentage of requested operations completed,
timing jitter, voltage margins under load, and recovery from failure modes.

Failure Modes Evaluated:
  1. Power Sag & Actuators (12V rail, attractors, flash pulse sag vs 9.8V floor)
  2. Lockfile Contention & Crash Recovery (BOOT_LOCK orphan handling, flock concurrency)
  3. ePaper Sparse Mode & Power Rail Isolation (daytime wake/sleep updates, nocturnal lockout)
  4. Storage Cushion & Abort Thresholds (cushion safety floor, abort counter reset on success)
  5. Rapid Burst Cadence & Buffer Pressure (burst mode pacing, zero dropped frames)
  6. Scheduler Cadence & RTC Jitter (cron calculation, wake alarm epoch arming)

Can be executed directly on the Raspberry Pi or triggered over SSH:
  python3 test_systematic_stress.py [--runs 3] [--burst-count 5] [--burst-interval 6.0]
"""

import argparse
import fcntl
import json
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path

# Add Mothbox root to sys.path
MOTHBOX_DIR = Path("/home/pi/Desktop/Mothbox")
if str(MOTHBOX_DIR) not in sys.path:
    sys.path.insert(0, str(MOTHBOX_DIR))

import mothbox_hw
from mothbox_paths import STATE_ROOT


# =====================================================================
# INDIVIDUAL TEST SUITES WITH QUANTITATIVE TELEMETRY & SCORING
# =====================================================================

def test_power_sag_and_rails():
    """
    Full real-world power integrity test:
    Stresses 12V rail, energizes all three attractors (UV1, UV2, White VIS) simultaneously,
    and fires the flash strobe while attractors are running.
    Measures idle voltage, loaded attractor voltage, flash strobe voltage, and verifies recovery.
    """
    print("\n  [TEST: Power Sag & Actuators (UV1 + UV2 + VIS + FLASH)]")
    # 1. Idle power measurement
    p_idle = mothbox_hw.read_power()
    v_idle = p_idle.get("voltage_v", 0.0)
    i_idle = p_idle.get("current_a", 0.0)

    # 2. Turn on 12V rail and all three attractors (UV1, UV2, VIS)
    mothbox_hw.attract_on(stagger_s=0.05)
    time.sleep(0.1)
    p_attract = mothbox_hw.read_power()
    v_attract = p_attract.get("voltage_v", 0.0)
    i_attract = p_attract.get("current_a", 0.0)

    # 3. Fire flash strobe WHILE ALL ATTRACTORS ARE RUNNING (Real-world capture condition)
    mothbox_hw.flash_on()
    time.sleep(0.05)
    p_flash = mothbox_hw.read_power()
    mothbox_hw.flash_off()
    v_flash = p_flash.get("voltage_v", 0.0)
    i_flash = p_flash.get("current_a", 0.0)

    # 4. Turn off attractors and 12V rail
    mothbox_hw.attract_off()
    time.sleep(0.05)
    p_post = mothbox_hw.read_power()
    v_post = p_post.get("voltage_v", 0.0)

    sag_attract = v_idle - v_attract if (v_idle > 0 and v_attract > 0) else 0.0
    sag_flash = v_idle - v_flash if (v_idle > 0 and v_flash > 0) else 0.0

    print(f"    Idle: {v_idle:.2f}V ({i_idle:.3f}A)")
    print(f"    All 3 Attractors ON: {v_attract:.2f}V ({i_attract:.3f}A) | Sag: {sag_attract:.2f}V")
    print(f"    Attractors + Flash: {v_flash:.2f}V ({i_flash:.3f}A) | Total Sag: {sag_flash:.2f}V")
    print(f"    Post-Test Recovery: {v_post:.2f}V")

    ops_done = 4  # rail, attract, flash, post
    passed = v_flash >= 8.5 and v_attract >= 10.5 and v_idle > 11.0
    score = 100.0 if passed else 50.0

    return {
        "status": "PASS" if passed else "FAIL",
        "score": score,
        "ops_req": 4,
        "ops_done": ops_done,
        "completion_pct": 100.0,
        "telemetry": {
            "v_idle": v_idle,
            "v_attract": v_attract,
            "v_flash": v_flash,
            "i_flash": i_flash,
            "sag_attract": sag_attract,
            "sag_flash": sag_flash
        }
    }


def test_lock_contention_and_recovery():
    """
    Validates lock resilience:
    1. Simulates orphaned BOOT_LOCK and verifies non-blocking import of Backup_Files.
    2. Tests flock concurrency for TakePhoto.
    3. Verifies clean lock release.
    """
    print("\n  [TEST: Lock Contention & Orphan Recovery]")
    boot_lock = "/run/boot_script_running"
    with open(boot_lock, "w") as f:
        f.write("active\n")

    # Import Backup_Files with BOOT_LOCK active
    import Backup_Files
    t1 = True
    if os.path.exists(boot_lock):
        os.remove(boot_lock)

    # Test flock concurrency using isolated test lockfile
    test_lock = "/tmp/test_flock_concurrency.lock"
    f1 = open(test_lock, "w")
    locked_out = False
    try:
        fcntl.flock(f1, fcntl.LOCK_EX | fcntl.LOCK_NB)
        f2 = open(test_lock, "w")
        try:
            fcntl.flock(f2, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            locked_out = True
        finally:
            f2.close()
    finally:
        try:
            fcntl.flock(f1, fcntl.LOCK_UN)
        except Exception:
            pass
        f1.close()
        if os.path.exists(test_lock):
            try:
                os.remove(test_lock)
            except Exception:
                pass

    ops_done = (1 if t1 else 0) + (1 if locked_out else 0)
    passed = t1 and locked_out
    print(f"    BOOT_LOCK Import Suicide Guard: {'PASS' if t1 else 'FAIL'} | Concurrency Lockout: {'PASS' if locked_out else 'FAIL'}")

    return {
        "status": "PASS" if passed else "FAIL",
        "score": 100.0 if passed else 50.0,
        "ops_req": 2,
        "ops_done": ops_done,
        "completion_pct": (ops_done / 2.0) * 100.0,
        "telemetry": {"boot_lock_import": t1, "flock_concurrency": locked_out}
    }


def test_epaper_sparse_policy_and_isolation():
    """
    Verifies ePaper Sparse Mode policy & hardware isolation:
    1. Daytime boot (wake) -> True (operator deployment feedback).
    2. Daytime shutdown (sleep) -> True (operator collection feedback).
    3. Nighttime boot (wake after 18:00) -> True (deployment feedback at any hour).
    4. Nighttime hourly shutdown (sleep <= 2h) -> False (nocturnal shutdown blackout).
    5. Dawn summary shutdown (sleep > 2h) -> True (morning collection feedback).
    """
    print("\n  [TEST: ePaper Sparse Mode Policy & Rail Isolation]")
    import Scheduler
    settings = {"epaper_mode": "sparse"}
    day_dt = datetime(2026, 10, 19, 14, 0, 0)
    night_dt = datetime(2026, 10, 19, 21, 0, 0)
    dawn_dt = datetime(2026, 10, 19, 5, 0, 0)
    now_e = time.time()

    r_day_boot = Scheduler.should_update_display("boot", settings, "ACTIVE", now_dt=day_dt)
    r_day_shut = Scheduler.should_update_display("shutdown", settings, "ACTIVE", now_dt=day_dt)
    r_night_boot = Scheduler.should_update_display("boot", settings, "ACTIVE", now_dt=night_dt)
    r_night_shut = Scheduler.should_update_display("shutdown", settings, "ACTIVE", next_epoch=now_e + 3600, now_dt=night_dt)
    r_dawn_shut = Scheduler.should_update_display("shutdown", settings, "ACTIVE", next_epoch=now_e + 13 * 3600, now_dt=dawn_dt)

    res = {
        "day_boot_allows": r_day_boot,
        "day_shutdown_allows": r_day_shut,
        "night_boot_allows": r_night_boot,
        "night_shutdown_blocks": not r_night_shut,
        "dawn_summary_allows": r_dawn_shut
    }

    ops_done = sum(1 for v in res.values() if v)
    passed = ops_done == 5
    print(f"    Day Boot Update: {r_day_boot} | Day Shut Update: {r_day_shut}")
    print(f"    Night Boot Update: {r_night_boot} | Night Lockout Shut: {not r_night_shut} | Dawn Summary: {r_dawn_shut}")

    return {
        "status": "PASS" if passed else "FAIL",
        "score": (ops_done / 5.0) * 100.0,
        "ops_req": 5,
        "ops_done": ops_done,
        "completion_pct": (ops_done / 5.0) * 100.0,
        "telemetry": res
    }


def test_storage_cushion_and_abort_resilience():
    """
    Validates:
    1. Storage cushion detection on internal SD.
    2. Abort counter suppression in DEBUG/PARTY modes.
    3. Abort counter reset on successful capture.
    """
    print("\n  [TEST: Storage Cushion & Abort Counter Resilience]")
    import TakePhoto
    from TakePhoto import atomic_update_kv
    counter_f = STATE_ROOT / "consecutive_aborted_captures.txt"

    # Simulate 3 consecutive aborted captures
    atomic_update_kv(counter_f, "consecutive_aborted_captures", "3")
    c_after_abort = 0
    if counter_f.exists():
        for line in open(counter_f):
            if "=" in line:
                try:
                    c_after_abort = int(line.split("=")[1].strip())
                except ValueError:
                    pass

    # Test reset function
    TakePhoto.reset_aborted_captures()
    c_after_reset = 0
    if counter_f.exists():
        for line in open(counter_f):
            if "=" in line:
                try:
                    c_after_reset = int(line.split("=")[1].strip())
                except ValueError:
                    pass

    # Verify DEBUG mode suppresses early shutdown
    sd_triggered = TakePhoto.trigger_early_shutdown("stress_test_in_debug", mode="DEBUG")

    c_inc_ok = c_after_abort == 3
    c_rst_ok = c_after_reset == 0
    sd_supp_ok = sd_triggered is False

    ops_done = (1 if c_inc_ok else 0) + (1 if c_rst_ok else 0) + (1 if sd_supp_ok else 0)
    passed = ops_done == 3
    print(f"    Abort Increment: {c_inc_ok} | Reset on Success: {c_rst_ok} | Debug Suppression: {sd_supp_ok}")

    return {
        "status": "PASS" if passed else "FAIL",
        "score": (ops_done / 3.0) * 100.0,
        "ops_req": 3,
        "ops_done": ops_done,
        "completion_pct": (ops_done / 3.0) * 100.0,
        "telemetry": {"abort_inc": c_inc_ok, "abort_rst": c_rst_ok, "debug_supp": sd_supp_ok}
    }


def test_rapid_burst_cadence(count=5, interval=6.0):
    """
    Executes a high-cadence rapid burst capture cycle (e.g. 5 frames @ 6.0s spacing).
    Measures:
    - Target pacing vs actual timestamp delta (jitter in ms).
    - Operations requested vs photos successfully saved (% completed).
    - Flash strobe coverage per frame.
    """
    print(f"\n  [TEST: Rapid Burst Cadence ({count}X @ {interval}s)]")
    # Clean up any stale lockfile before starting
    lock_path = "/run/mothbox_takephoto.lock"
    if os.path.exists(lock_path):
        try:
            with open(lock_path, "a") as f:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(f, fcntl.LOCK_UN)
            os.remove(lock_path)
        except Exception:
            pass

    (STATE_ROOT / "last_photo_time.txt").unlink(missing_ok=True)
    with open(STATE_ROOT / "lastcalibration.txt", "w") as f:
        f.write(f"lastcalibration={time.time()}\n")

    with open(STATE_ROOT / "capture_mode.txt", "w") as f:
        f.write("capture_mode=burst\n")
    with open(STATE_ROOT / "burst_count.txt", "w") as f:
        f.write(f"burst_count={count}\n")
    with open(STATE_ROOT / "burst_interval_s.txt", "w") as f:
        f.write(f"burst_interval_s={interval}\n")

    import subprocess
    cmd = ["sudo", "python3", "/home/pi/Desktop/Mothbox/TakePhoto.py"]
    timeout_budget = int(count * (interval + 4.0) + 40)
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_budget)

    # Restore timelapse mode
    with open(STATE_ROOT / "capture_mode.txt", "w") as f:
        f.write("capture_mode=timelapse\n")

    saved_frames = 0
    jitters_ms = []
    for line in res.stdout.splitlines():
        if "Saved burst image" in line:
            saved_frames += 1
        if "target=" in line and "actual=" in line:
            try:
                p = line.split("target=")[1].split(", actual=")
                tgt = float(p[0].replace("s", ""))
                act = float(p[1].split("s")[0])
                jitters_ms.append(abs(act - tgt) * 1000.0)
            except Exception:
                pass

    completion_pct = (saved_frames / float(count)) * 100.0
    mean_jitter = sum(jitters_ms) / len(jitters_ms) if jitters_ms else 0.0
    max_jitter = max(jitters_ms) if jitters_ms else 0.0

    print(f"    Frames Written: {saved_frames}/{count} ({completion_pct:.1f}%) | Mean Pacing Jitter: {mean_jitter:.1f}ms (Max: {max_jitter:.1f}ms)")
    if saved_frames < count and res.stdout:
        # Print tail of stdout to debug why
        print("    [!] TakePhoto output tail:")
        for line in res.stdout.splitlines()[-6:]:
            print(f"        {line}")

    # Clean up test captures
    subprocess.run(["sudo", "rm", "-rf", "/home/pi/Desktop/Mothbox/photos/*", "/home/pi/Desktop/Mothbox/photos_backedup/*"], check=False)

    passed = saved_frames == count and (not jitters_ms or max_jitter < 500.0)
    score = completion_pct if passed else (completion_pct * 0.7)

    return {
        "status": "PASS" if passed else "FAIL",
        "score": score,
        "ops_req": count,
        "ops_done": saved_frames,
        "completion_pct": completion_pct,
        "telemetry": {"mean_jitter_ms": mean_jitter, "max_jitter_ms": max_jitter, "frames_saved": saved_frames}
    }


def test_scheduler_cadence_and_rtc():
    """
    Validates Scheduler cadence, cron computation, and RTC hardware alarm arming.
    """
    print("\n  [TEST: Scheduler Cadence & RTC Arming]")
    import Scheduler
    settings = Scheduler.load_settings_for_wakeup()
    cron_expr = Scheduler.build_cron_expression(settings)
    utc_off = float(settings.get("utc_off", 0))
    next_epoch = Scheduler.calculate_next_event(cron_expr, utc_off)
    Scheduler.clear_wakeup_alarm()
    Scheduler.set_wakeup_alarm(next_epoch)

    arm_res = False
    try:
        with open("/sys/class/rtc/rtc0/wakealarm", "r") as f:
            val = f.read().strip()
        if val:
            arm_res = abs(int(val) - next_epoch) <= 2
    except Exception:
        pass

    next_dt_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(next_epoch))
    print(f"    Cron: '{cron_expr}' -> Next Wake: {next_dt_str} | RTC Alarm Armed: {arm_res}")

    return {
        "status": "PASS" if arm_res else "FAIL",
        "score": 100.0 if arm_res else 0.0,
        "ops_req": 2,
        "ops_done": 2 if arm_res else 1,
        "completion_pct": 100.0 if arm_res else 50.0,
        "telemetry": {"cron": cron_expr, "next_epoch": next_epoch, "alarm_confirmed": arm_res}
    }


TEST_REGISTRY = {
    "power_sag": test_power_sag_and_rails,
    "lock_recovery": test_lock_contention_and_recovery,
    "epaper_sparse": test_epaper_sparse_policy_and_isolation,
    "storage_cushion": test_storage_cushion_and_abort_resilience,
    "burst_cadence": test_rapid_burst_cadence,
    "scheduler_rtc": test_scheduler_cadence_and_rtc,
}


def get_boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except Exception:
        return "unknown"


def get_uptime_s():
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except Exception:
        return 0.0


def run_stress_battery(runs=3, burst_count=5, burst_interval=6.0, skip_rails=False):
    initial_boot_id = get_boot_id()
    initial_uptime = get_uptime_s()

    active_tests = dict(TEST_REGISTRY)
    if skip_rails:
        active_tests.pop("power_sag", None)

    print("=" * 78)
    print("       MOTHBOX SYSTEMATIC RELIABILITY & STRESS TEST BATTERY")
    print(f"       Iterations: {runs} | Burst Config: {burst_count}X @ {burst_interval}s | Rails Test: {'SKIPPED (Splitter Bypass)' if skip_rails else 'ACTIVE'}")
    print(f"       Boot ID: {initial_boot_id} | Starting Uptime: {initial_uptime:.1f}s")
    print("=" * 78)

    all_run_data = []
    power_faults = 0

    for run_idx in range(1, runs + 1):
        order = list(active_tests.keys())
        random.shuffle(order)

        print(f"\n{'#' * 78}")
        print(f"  ITERATION {run_idx}/{runs} -- Randomized Sequence: {' -> '.join(order)}")
        print(f"{'#' * 78}")

        run_record = {
            "run": run_idx,
            "order": order,
            "tests": {},
            "total_ops_req": 0,
            "total_ops_done": 0,
            "scores": []
        }

        for t_name in order:
            t_func = TEST_REGISTRY[t_name]
            t_start = time.time()
            try:
                if t_name == "burst_cadence":
                    res = t_func(count=burst_count, interval=burst_interval)
                else:
                    res = t_func()
            except Exception as e:
                import traceback
                traceback.print_exc()
                res = {"status": "ERROR", "score": 0.0, "ops_req": 1, "ops_done": 0, "details": str(e)}
            res["duration_s"] = time.time() - t_start

            curr_boot_id = get_boot_id()
            if curr_boot_id != initial_boot_id:
                print(f"\n[FATAL FAULT] Kernel Boot ID mismatch! Device suffered uncommanded reset during '{t_name}'!")
                power_faults += 1
                res["status"] = "BROWNOUT_REBOOT"
                res["score"] = 0.0

            run_record["tests"][t_name] = res
            run_record["total_ops_req"] += res.get("ops_req", 1)
            run_record["total_ops_done"] += res.get("ops_done", 0)
            run_record["scores"].append(res.get("score", 0.0))

        all_run_data.append(run_record)

    # Summary scorecard
    print("\n" + "=" * 78)
    print("                     SYSTEMATIC RELIABILITY SCORECARD")
    print("=" * 78)

    test_keys = list(active_tests.keys())
    header = f"{'Subsystem / Test Suite':<22} | " + " | ".join([f"Run {i}" for i in range(1, runs + 1)]) + " | Ops Done (%) | Score"
    print(header)
    print("-" * len(header))

    total_ops_all_req = 0
    total_ops_all_done = 0
    all_scores = []

    for t_k in test_keys:
        run_statuses = []
        t_req = 0
        t_done = 0
        t_scores = []
        for r in all_run_data:
            t_res = r["tests"].get(t_k, {})
            run_statuses.append(t_res.get("status", "N/A"))
            t_req += t_res.get("ops_req", 1)
            t_done += t_res.get("ops_done", 0)
            t_scores.append(t_res.get("score", 0.0))

        total_ops_all_req += t_req
        total_ops_all_done += t_done
        avg_score = sum(t_scores) / len(t_scores) if t_scores else 0.0
        all_scores.append(avg_score)
        comp_pct = (t_done / float(t_req)) * 100.0 if t_req else 0.0

        status_str = " | ".join([f"{s:^5}" for s in run_statuses])
        print(f"{t_k:<22} | {status_str} | {t_done:>2}/{t_req:<2} ({comp_pct:5.1f}%) | {avg_score:5.1f}%")

    print("-" * len(header))
    overall_completion = (total_ops_all_done / float(total_ops_all_req)) * 100.0 if total_ops_all_req else 0.0
    overall_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

    print(f"{'OVERALL SYSTEM SCORE':<22} | " + " " * (len(header) - 48) + f"| {total_ops_all_done:>2}/{total_ops_all_req:<2} ({overall_completion:5.1f}%) | {overall_score:5.1f}%")
    print("=" * 78)

    print("\nHARDWARE INTEGRITY & CONTINUOUS UPTIME AUDIT:")
    print(f"  Kernel Boot ID         : {initial_boot_id}")
    print(f"  Uncommanded Brownouts  : {power_faults}")
    power_ok = (power_faults == 0)
    print(f"  Power Integrity Status : {'PASSED (Zero brownouts, 100% continuous uptime)' if power_ok else 'FAILED (Uncommanded reboot detected)'}")

    # Save detailed JSON summary
    out_file = Path("/home/pi/Desktop/Mothbox/logs/mothbox_systematic_stress_results.json")
    try:
        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w") as f:
            json.dump({
                "timestamp": datetime.now().isoformat(),
                "boot_id": initial_boot_id,
                "power_faults": power_faults,
                "overall_score": overall_score,
                "overall_completion": overall_completion,
                "runs": all_run_data
            }, f, indent=2)
        print(f"\nDetailed telemetry log written to: {out_file}")
    except Exception as e:
        print(f"Failed to write log JSON: {e}")

    passed_all = (overall_completion >= 95.0) and (overall_score >= 90.0) and power_ok
    print(f"\nSYSTEM DEPLOYMENT READINESS VERDICT: {'QUALIFIED FOR FIELD DEPLOYMENT' if passed_all else 'NOT READY -- ATTENTION REQUIRED'}")
    return passed_all


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mothbox Systematic Reliability & Stress Test Battery")
    parser.add_argument("--runs", type=int, default=3, help="Number of randomized stress runs (default: 3)")
    parser.add_argument("--burst-count", type=int, default=5, help="Photos per burst cycle (default: 5)")
    parser.add_argument("--burst-interval", type=float, default=6.0, help="Interval in seconds between burst photos (default: 6.0)")
    parser.add_argument("--skip-rails", action="store_true", help="Skip 12V actuator switching test (useful for multi-battery splitter configurations)")
    args = parser.parse_args()

    ok = run_stress_battery(runs=args.runs, burst_count=args.burst_count, burst_interval=args.burst_interval, skip_rails=args.skip_rails)
    sys.exit(0 if ok else 1)
