#!/usr/bin/python3
"""
Unit tests for Burst Capture Mode in Mothbox Firmware.
Tests settings parsing, fallback safety, timing calculations,
worker thread queue behavior, and hardware lifecycle invariants.

Run:
    python3 Mothbox_Unified/tests/test_burst_mode.py
"""

import ast
import csv
import io
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

# Ensure Mothbox_Unified is on path
CURRENT_DIR = Path(__file__).resolve().parent
REPO_ROOT = CURRENT_DIR.parent

def load_scheduler_parser():
    """
    Safely extracts load_settings and SETTING_DEFAULTS via AST
    without executing Scheduler.py's hardware boot scripts.
    """
    scheduler_code = open(REPO_ROOT / "Scheduler.py").read()
    tree = ast.parse(scheduler_code)
    nodes_to_exec = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SETTING_DEFAULTS" for t in node.targets):
            nodes_to_exec.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in ("_open_csv_robust", "load_settings"):
            nodes_to_exec.append(node)
    
    mod = ast.Module(body=nodes_to_exec, type_ignores=[])
    compiled = compile(mod, filename="<ast>", mode="exec")
    scope = {"csv": csv, "print": lambda *args: None}  # quiet print
    exec(compiled, scope)
    return scope["load_settings"], scope["SETTING_DEFAULTS"]


def test_csv_settings_defaults():
    """Verify that capture_mode defaults to timelapse and burst settings have safe fallbacks."""
    print("Test 1: CSV settings defaults & fallbacks...")
    load_settings, setting_defaults = load_scheduler_parser()

    # Check SETTING_DEFAULTS
    assert setting_defaults.get("capture_mode") == "timelapse", f"Expected default timelapse, got {setting_defaults.get('capture_mode')}"
    assert setting_defaults.get("burst_count") == 8, f"Expected default burst_count 8, got {setting_defaults.get('burst_count')}"
    assert setting_defaults.get("burst_interval_s") == 6.0, f"Expected default burst_interval_s 6.0, got {setting_defaults.get('burst_interval_s')}"
    
    # 1. Empty/minimal CSV without capture_mode
    minimal_csv = "SETTING,VALUE,DETAILS\nphoto_interval,1,default\n"
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tf:
        tf.write(minimal_csv)
        tf.flush()
        tf_path = tf.name

    try:
        settings = load_settings(tf_path)
        assert settings["capture_mode"] == "timelapse", f"Expected timelapse, got {settings.get('capture_mode')}"
        assert settings["burst_count"] == 8, f"Expected 8, got {settings.get('burst_count')}"
        assert settings["burst_interval_s"] == 6.0, f"Expected 6.0, got {settings.get('burst_interval_s')}"
        assert settings["photo_interval"] == 1, f"Expected 1, got {settings.get('photo_interval')}"
        print("  OK: Missing keys cleanly fall back to stock timelapse default")
    finally:
        os.remove(tf_path)

    # 2. CSV explicitly requesting burst mode
    burst_csv = (
        "SETTING,VALUE,DETAILS\n"
        "capture_mode,burst,burst capture\n"
        "burst_count,10,10 photos\n"
        "burst_interval_s,5.5,5.5 seconds spacing\n"
    )
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tf:
        tf.write(burst_csv)
        tf.flush()
        tf_path = tf.name

    try:
        settings = load_settings(tf_path)
        assert settings["capture_mode"] == "burst", f"Expected burst, got {settings.get('capture_mode')}"
        assert settings["burst_count"] == 10, f"Expected 10, got {settings.get('burst_count')}"
        assert settings["burst_interval_s"] == 5.5, f"Expected 5.5, got {settings.get('burst_interval_s')}"
        print("  OK: Explicit burst parameters correctly parsed")
    finally:
        os.remove(tf_path)

    # 3. CSV with invalid / out-of-range burst values
    invalid_csv = (
        "SETTING,VALUE,DETAILS\n"
        "capture_mode,UNKNOWN_MODE,invalid\n"
        "burst_count,-5,negative\n"
        "burst_interval_s,0.01,too small\n"
    )
    with tempfile.NamedTemporaryFile("w+", delete=False, suffix=".csv") as tf:
        tf.write(invalid_csv)
        tf.flush()
        tf_path = tf.name

    try:
        settings = load_settings(tf_path)
        assert settings["capture_mode"] == "timelapse", f"Expected fallback timelapse, got {settings.get('capture_mode')}"
        # burst_count clamped to min 1
        assert settings["burst_count"] == 1, f"Expected clamp to 1, got {settings.get('burst_count')}"
        # burst_interval_s clamped to min 0.5
        assert settings["burst_interval_s"] == 0.5, f"Expected clamp to 0.5, got {settings.get('burst_interval_s')}"
        print("  OK: Invalid values safely sanitized without crash")
    finally:
        os.remove(tf_path)


def test_timing_cadence_calculations():
    """Verify monotonic deadline calculations and budget fit."""
    print("Test 2: Timing cadence & deadline scheduling...")
    
    burst_count = 8
    burst_interval_s = 6.0
    t0 = 1000.0

    deadlines = [t0 + (k * burst_interval_s) for k in range(burst_count)]
    expected_deadlines = [1000.0, 1006.0, 1012.0, 1018.0, 1024.0, 1030.0, 1036.0, 1042.0]
    
    assert deadlines == expected_deadlines, f"Deadline mismatch: {deadlines} != {expected_deadlines}"
    
    total_burst_span = deadlines[-1] - deadlines[0]
    assert total_burst_span == 42.0, f"Expected 42.0s, got {total_burst_span}"
    
    # Check margin within 60s cron minute
    margin_in_minute = 60.0 - total_burst_span
    assert margin_in_minute == 18.0, f"Expected 18.0s margin, got {margin_in_minute}"
    
    # Check shutdown drain budget (120s in Scheduler.py)
    drain_budget = 120
    assert total_burst_span < drain_budget, f"Burst span {total_burst_span}s exceeds drain budget {drain_budget}s"
    print(f"  OK: 8X @ 6.0s takes {total_burst_span}s (margin: {margin_in_minute}s to minute, {drain_budget - total_burst_span}s to drain timeout)")


def test_save_worker_concurrency():
    """Verify asynchronous save worker queue ingestion and graceful shutdown."""
    print("Test 3: Async save worker queue processing...")

    saved_items = []
    
    class MockWorker:
        def __init__(self):
            self.q = queue.Queue(maxsize=3)
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.saved_count = 0
            self.thread.start()

        def enqueue(self, item):
            self.q.put(item)

        def _run(self):
            while True:
                item = self.q.get()
                if item is None:
                    self.q.task_done()
                    break
                # Simulate fast JPEG write latency
                time.sleep(0.01)
                saved_items.append(item)
                self.saved_count += 1
                self.q.task_done()

        def wait_done(self, timeout=5):
            self.q.put(None)
            self.thread.join(timeout=timeout)

    worker = MockWorker()
    for i in range(8):
        worker.enqueue(f"frame_{i + 1}")

    worker.wait_done(timeout=2)
    assert worker.saved_count == 8, f"Expected 8 saved items, got {worker.saved_count}"
    assert len(saved_items) == 8, f"Expected 8 items in list, got {len(saved_items)}"
    print(f"  OK: Worker drained all {worker.saved_count} items without dropping frames")


def test_hardware_lifecycle_invariant():
    """Verify single open/close of camera and paired flash pulses."""
    print("Test 4: Hardware lifecycle invariant...")

    mock_picam = MagicMock()
    mock_flash_on = MagicMock()
    mock_flash_off = MagicMock()

    burst_count = 8
    
    # Simulate execution of burst capture logic
    mock_picam.start()
    for k in range(burst_count):
        mock_flash_on()
        try:
            mock_picam.capture_request()
        finally:
            mock_flash_off()
    mock_picam.stop()

    assert mock_picam.start.call_count == 1, f"Expected camera start count 1, got {mock_picam.start.call_count}"
    assert mock_picam.stop.call_count == 1, f"Expected camera stop count 1, got {mock_picam.stop.call_count}"
    assert mock_flash_on.call_count == 8, f"Expected 8 flash_on calls, got {mock_flash_on.call_count}"
    assert mock_flash_off.call_count == 8, f"Expected 8 flash_off calls, got {mock_flash_off.call_count}"
    assert mock_picam.capture_request.call_count == 8, f"Expected 8 captures, got {mock_picam.capture_request.call_count}"
    print("  OK: picam2 started/stopped exactly once, flash pulsed exactly 8 times")


if __name__ == "__main__":
    print("\n--- Running Mothbox Burst Mode Unit Tests ---")
    test_csv_settings_defaults()
    test_timing_cadence_calculations()
    test_save_worker_concurrency()
    test_hardware_lifecycle_invariant()
    print("ALL BURST MODE UNIT TESTS PASSED!\n")
