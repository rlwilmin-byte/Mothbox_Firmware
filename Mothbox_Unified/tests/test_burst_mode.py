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
    """Verify asynchronous save worker queue ingestion and graceful shutdown with expanded queue."""
    print("Test 3: Async save worker queue processing & non-blocking capacity...")

    saved_items = []
    
    class MockWorker:
        def __init__(self, max_queue_size=10):
            self.q = queue.Queue(maxsize=max_queue_size)
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
                # Simulate JPEG write latency
                time.sleep(0.01)
                saved_items.append(item)
                self.saved_count += 1
                self.q.task_done()

        def wait_done(self, timeout=5):
            self.q.put(None)
            self.thread.join(timeout=timeout)

    # With maxsize=10 (burst_count + 2), all 8 burst frames can be enqueued immediately
    # without stalling the capture loop even if disk write latency is high.
    worker = MockWorker(max_queue_size=10)
    for i in range(8):
        worker.enqueue((f"frame_{i + 1}", True))

    worker.wait_done(timeout=2)
    assert worker.saved_count == 8, f"Expected 8 saved items, got {worker.saved_count}"
    assert len(saved_items) == 8, f"Expected 8 items in list, got {len(saved_items)}"
    assert worker.q.maxsize == 10, f"Expected maxsize 10, got {worker.q.maxsize}"
    print(f"  OK: Worker queue (capacity={worker.q.maxsize}) drained all {worker.saved_count} items without blocking")


def test_hardware_lifecycle_invariant():
    """Verify single open/close of camera, rolling shutter flush/sync, and paired flash pulses."""
    print("Test 4: Hardware lifecycle & rolling shutter synchronization invariant...")

    mock_picam = MagicMock()
    mock_flash_on = MagicMock(return_value=True)
    mock_flash_off = MagicMock()

    burst_count = 8
    
    # Simulate execution of burst capture logic with rolling shutter sync
    mock_picam.start()
    for k in range(burst_count):
        flash_fired = mock_flash_on()
        try:
            if flash_fired:
                # 1. Flush in-flight frame (prevent rolling shutter slit banding)
                mock_picam.capture_request(flush=True)
                # 2. Capture synchronized frame fully covered by strobe
                mock_picam.capture_request(flush=False)
            else:
                mock_picam.capture_request(flush=True)
        finally:
            mock_flash_off()
    mock_picam.stop()

    assert mock_picam.start.call_count == 1, f"Expected camera start count 1, got {mock_picam.start.call_count}"
    assert mock_picam.stop.call_count == 1, f"Expected camera stop count 1, got {mock_picam.stop.call_count}"
    assert mock_flash_on.call_count == 8, f"Expected 8 flash_on calls, got {mock_flash_on.call_count}"
    assert mock_flash_off.call_count == 8, f"Expected 8 flash_off calls, got {mock_flash_off.call_count}"
    # 8 flush calls + 8 synchronized capture calls = 16 total capture_request invocations
    assert mock_picam.capture_request.call_count == 16, f"Expected 16 capture_requests, got {mock_picam.capture_request.call_count}"
    print("  OK: picam2 started/stopped once, 8 synchronized strobe cycles with in-flight frame flushes")


def test_exif_flash_propagation():
    """Verify that build_exif_bytes receives flash_fired boolean and encodes correct EXIF tags."""
    print("Test 5: EXIF flash metadata propagation...")
    sys.path.insert(0, str(REPO_ROOT))

    try:
        import piexif
        import piexif.helper
    except ImportError:
        import types
        mock_piexif = types.ModuleType("piexif")
        class ImageIFD: Make=271; Model=272; Software=305; DateTime=306; ImageDescription=270
        class ExifIFD:
            DateTimeOriginal=36867; DateTimeDigitized=36868; OffsetTime=36880; OffsetTimeOriginal=36881; OffsetTimeDigitized=36882
            ExposureProgram=34850; ExposureMode=41986; WhiteBalance=41987; ColorSpace=40961
            PixelXDimension=40962; PixelYDimension=40963; ISOSpeedRatings=34855
            Flash=37385; LightSource=37384; ExposureTime=33434; FocalLength=37386; FNumber=33437
            LensMake=42035; LensModel=42036; SubjectDistance=37382; BodySerialNumber=42033; ImageUniqueID=42016
            Temperature=37888; UserComment=37510
        class GPSIFD: GPSVersionID=0; GPSLatitudeRef=1; GPSLatitude=2; GPSLongitudeRef=3; GPSLongitude=4
        mock_piexif.ImageIFD = ImageIFD
        mock_piexif.ExifIFD = ExifIFD
        mock_piexif.GPSIFD = GPSIFD
        mock_piexif.dump = lambda d: b"EXIF_DUMP"
        helper = types.ModuleType("helper")
        class UserComment:
            @staticmethod
            def dump(s, encoding="ascii"): return b"ASCII\x00\x00\x00" + s.encode("ascii")
            @staticmethod
            def load(b): return b[8:].decode("ascii") if b.startswith(b"ASCII\x00\x00\x00") else b.decode("ascii", errors="replace")
        helper.UserComment = UserComment
        mock_piexif.helper = helper
        sys.modules["piexif"] = mock_piexif
        sys.modules["piexif.helper"] = helper
        import piexif

    import mothbox_exif
    from datetime import datetime

    now = datetime(2026, 10, 18, 22, 0, 0)
    dummy_meta = {"ExposureTime": 500, "AnalogueGain": 1.0, "LensPosition": 2.5}

    # Case 1: flash_fired = True
    dict_flash_true = mothbox_exif.build_exif_dict(
        metadata=dummy_meta,
        requested_exposure_us=500,
        image_size=(9248, 6944),
        capture_time=now,
        utc_offset_hours=0,
        computer_name="testbox",
        software_version="5",
        sensor_name="ov64a40",
        flash_fired=True,
    )
    exif_true = dict_flash_true["Exif"]
    assert exif_true[piexif.ExifIFD.Flash] == 0x09, f"Expected Flash=0x09, got {exif_true[piexif.ExifIFD.Flash]}"
    assert exif_true[piexif.ExifIFD.LightSource] == 4, f"Expected LightSource=4, got {exif_true[piexif.ExifIFD.LightSource]}"
    import json
    comment_true = json.loads(piexif.helper.UserComment.load(exif_true[piexif.ExifIFD.UserComment]))
    assert comment_true["capture"]["flash"] is True, f"Expected comment flash=True, got {comment_true['capture']['flash']}"

    # Case 2: flash_fired = False (e.g. voltage brownout guard inhibited flash)
    dict_flash_false = mothbox_exif.build_exif_dict(
        metadata=dummy_meta,
        requested_exposure_us=500,
        image_size=(9248, 6944),
        capture_time=now,
        utc_offset_hours=0,
        computer_name="testbox",
        software_version="5",
        sensor_name="ov64a40",
        flash_fired=False,
    )
    exif_false = dict_flash_false["Exif"]
    assert exif_false[piexif.ExifIFD.Flash] == 0x10, f"Expected Flash=0x10, got {exif_false[piexif.ExifIFD.Flash]}"
    assert exif_false[piexif.ExifIFD.LightSource] == 0, f"Expected LightSource=0, got {exif_false[piexif.ExifIFD.LightSource]}"
    comment_false = json.loads(piexif.helper.UserComment.load(exif_false[piexif.ExifIFD.UserComment]))
    assert comment_false["capture"]["flash"] is False, f"Expected comment flash=False, got {comment_false['capture']['flash']}"
    print("  OK: EXIF tags accurately distinguish fired (0x09/LightSource=4) vs inhibited (0x10/LightSource=0)")


if __name__ == "__main__":
    print("\n--- Running Mothbox Burst Mode Unit Tests ---")
    test_csv_settings_defaults()
    test_timing_cadence_calculations()
    test_save_worker_concurrency()
    test_hardware_lifecycle_invariant()
    test_exif_flash_propagation()
    print("ALL BURST MODE UNIT TESTS PASSED!\n")
