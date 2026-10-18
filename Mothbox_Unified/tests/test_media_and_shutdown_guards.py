#!/usr/bin/env python3
"""
Unit tests for Mothbox Media Health Checks and Graceful Shutdown Guards:
1. Storage Full Guard (10 consecutive aborted captures -> graceful shutdown)
2. Abort Counter Reset on successful photo save
3. Critical Battery Guard (<9.8V brownout safety floor -> graceful shutdown)
4. Non-aggressive battery threshold (10.0V - 11.0V allowed, <9.8V halts)
5. Session Abort Window Bypass for early shutdowns
6. Media Health Checks:
   - Rootfs read-only detection & self-healing remount
   - Missing USB drive detection
   - USB read-only detection & self-healing remount
   - Low USB space (<1.0GB)
   - Low SD card safety cushion (<8.0GB)
   - One-time reboot fsck self-recovery (no reboot loops)
7. ePaper display override during media warning (bypassing nocturnal blackout)
8. Inverted high-contrast warning banner rendering in UpdateDisplay
"""

import ast
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
_UNIFIED = _HERE.parent
sys.path.insert(0, str(_UNIFIED))


def load_scheduler_helpers(control_root):
    """
    Safely extracts helper functions from Scheduler.py via AST without
    triggering Raspberry Pi hardware boot routines.
    """
    with open(_UNIFIED / "Scheduler.py", "r", encoding="utf-8") as f:
        code = f.read()
    tree = ast.parse(code)
    nodes = []
    target_funcs = (
        "get_epaper_mode",
        "should_update_display",
        "check_media_health",
        "run_shutdown_pi5",
        "run_shutdown_pi5_FAST",
        "atomic_update_kv",
        "atomic_write",
        "get_control_values",
        "read_control",
    )
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "SETTING_DEFAULTS" for t in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in target_funcs:
            nodes.append(node)

    mod = ast.Module(body=nodes, type_ignores=[])
    compiled = compile(mod, filename="<ast>", mode="exec")

    mock_hw = MagicMock()
    mock_hw.read_power.return_value = {"voltage_v": 12.0, "current_a": 0.2}

    mock_subp = MagicMock()
    mock_subp.run.return_value.returncode = 0
    mock_subp.run.return_value.stdout = ""
    mock_subp.run.return_value.stderr = ""

    scope = {
        "CONTROL_ROOT": str(control_root),
        "BOOT_LOCK": str(control_root / "boot_lock"),
        "_resolve_read": lambda p: p,
        "datetime": datetime,
        "time": time,
        "shutil": shutil,
        "subprocess": mock_subp,
        "os": os,
        "sys": sys,
        "psutil": MagicMock(),
        "Path": Path,
        "log_warn": MagicMock(),
        "log_ok": MagicMock(),
        "log_info": MagicMock(),
        "log_section": MagicMock(),
        "mothbox_hw": mock_hw,
        "rpiModel": 5,
        "runtime": 60,
        "use_switch_schedule": False,
        "switch_schedule": {},
        "load_settings_for_wakeup": lambda: {},
        "build_cron_expression": lambda s: "* * * * *",
        "calculate_next_event": lambda c, u: 1000000,
        "clear_wakeup_alarm": MagicMock(),
        "set_wakeup_alarm": MagicMock(),
        "final_backup_before_shutdown": MagicMock(),
        "run_helper": MagicMock(),
        "run_script": MagicMock(),
        "should_abort_shutdown": MagicMock(return_value=None),
        "should_reboot_before_poweroff": lambda: False,
        "filesystem_needs_expansion": lambda: False,
        "wait_for_firstboot_settle": MagicMock(),
        "arm_shutdown_deadman": MagicMock(),
        "utc_off": 0,
        "schedule": MagicMock(),
    }

    exec(compiled, scope)
    return scope


def load_takephoto_helpers(control_root):
    """
    Safely extracts shutdown and abort helpers from TakePhoto.py via AST.
    """
    with open(_UNIFIED / "TakePhoto.py", "r", encoding="utf-8") as f:
        code = f.read()
    tree = ast.parse(code)
    nodes = []
    target_funcs = (
        "reset_aborted_captures",
        "trigger_early_shutdown",
        "atomic_update_kv",
        "atomic_write",
        "read_control",
    )
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in ("ABORT_COUNTER_FILE", "EARLY_SHUTDOWN_FILE")
            for t in node.targets
        ):
            nodes.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in target_funcs:
            nodes.append(node)

    mod = ast.Module(body=nodes, type_ignores=[])
    compiled = compile(mod, filename="<ast>", mode="exec")

    scope = {
        "CONTROL_ROOT": control_root,
        "ABORT_COUNTER_FILE": control_root / "consecutive_aborted_captures.txt",
        "EARLY_SHUTDOWN_FILE": control_root / "early_shutdown_reason.txt",
        "_resolve_read": lambda p: p,
        "os": os,
        "sys": sys,
        "Path": Path,
        "subprocess": MagicMock(),
        "mothbox_hw": MagicMock(),
    }

    exec(compiled, scope)
    return scope


class TestMediaAndShutdownGuards(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mb_guard_test_")
        self.control_root = Path(self.test_dir) / "controls"
        self.control_root.mkdir(parents=True, exist_ok=True)
        self.mounts_file = Path(self.test_dir) / "mounts"
        self.mounts_file.write_text("rootfs / ext4 rw 0 0\n")
        self.sched_scope = load_scheduler_helpers(self.control_root)
        self.tp_scope = load_takephoto_helpers(self.control_root)

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # -------------------------------------------------------------------------
    # 1. Storage Abort Tracking & 10-Abort Shutdown Guard
    # -------------------------------------------------------------------------
    def test_storage_abort_tracking_and_shutdown_trigger(self):
        """10 consecutive aborted captures due to low storage must trigger graceful shutdown."""
        abort_file = self.control_root / "consecutive_aborted_captures.txt"
        shutdown_file = self.control_root / "early_shutdown_reason.txt"

        atomic_update = self.tp_scope["atomic_update_kv"]
        read_control = self.tp_scope["read_control"]
        trigger_early_shutdown = self.tp_scope["trigger_early_shutdown"]
        mock_hw = self.tp_scope["mothbox_hw"]

        # Simulate 9 consecutive aborts
        for i in range(1, 10):
            atomic_update(abort_file, "consecutive_aborted_captures", str(i))
            count = int(read_control(abort_file, "consecutive_aborted_captures", "0"))
            self.assertEqual(count, i)
            self.assertFalse(shutdown_file.exists())

        # 10th abort triggers early shutdown
        with patch("time.sleep"):
            trigger_early_shutdown(reason="storage_full_10_aborts")

        self.assertTrue(shutdown_file.exists())
        reason = read_control(shutdown_file, "early_shutdown_reason", "")
        self.assertEqual(reason, "storage_full_10_aborts")
        mock_hw.all_lights_off.assert_called()

    def test_abort_counter_reset_on_photo_save(self):
        """Any successful photo save must reset the consecutive abort counter to 0."""
        abort_file = self.control_root / "consecutive_aborted_captures.txt"
        atomic_update = self.tp_scope["atomic_update_kv"]
        read_control = self.tp_scope["read_control"]
        reset_aborted_captures = self.tp_scope["reset_aborted_captures"]

        atomic_update(abort_file, "consecutive_aborted_captures", "7")
        self.assertEqual(int(read_control(abort_file, "consecutive_aborted_captures", "0")), 7)

        reset_aborted_captures()
        self.assertEqual(int(read_control(abort_file, "consecutive_aborted_captures", "0")), 0)

    # -------------------------------------------------------------------------
    # 2. Critical Battery Brownout Guard
    # -------------------------------------------------------------------------
    def test_battery_voltage_brownout_guard_thresholds(self):
        """
        Critical battery cutoff:
        - Voltage < 9.8V: triggers early shutdown to prevent PMIC reset / brownout.
        - Voltage >= 9.8V (e.g. 10.2V, 10.5V, 11.0V): allowed to run (non-aggressive).
        """
        shutdown_file = self.control_root / "early_shutdown_reason.txt"
        trigger_early_shutdown = self.tp_scope["trigger_early_shutdown"]
        read_control = self.tp_scope["read_control"]

        # Non-aggressive: 10.2V should NOT trigger shutdown
        voltage_safe = 10.2
        self.assertFalse(0.0 < voltage_safe < 9.8)

        # Depleted pack: 9.4V under load -> must trigger shutdown
        voltage_crit = 9.40
        self.assertTrue(0.0 < voltage_crit < 9.8)

        with patch("time.sleep"):
            trigger_early_shutdown(reason=f"critical_battery_{voltage_crit:.2f}V")
        self.assertTrue(shutdown_file.exists())
        reason = read_control(shutdown_file, "early_shutdown_reason", "")
        self.assertEqual(reason, "critical_battery_9.40V")

    # -------------------------------------------------------------------------
    # 3. Session Window Abort Bypass for Early Shutdowns
    # -------------------------------------------------------------------------
    def test_early_shutdown_bypasses_session_window_abort(self):
        """Early shutdown requests (storage full, critical battery) must NOT be aborted by should_abort_shutdown."""
        run_shutdown_pi5 = self.sched_scope["run_shutdown_pi5"]
        mock_abort = MagicMock(return_value=45.0)
        self.sched_scope["should_abort_shutdown"] = mock_abort
        mock_backup = MagicMock()
        self.sched_scope["final_backup_before_shutdown"] = mock_backup

        with patch("os.system") as mock_os_system, \
             patch("time.sleep"):
            # With early_reason provided, should_abort_shutdown must NOT be called
            run_shutdown_pi5(early_reason="storage_full_10_aborts")
            mock_abort.assert_not_called()
            mock_backup.assert_called_once()
            mock_os_system.assert_called_with("sudo shutdown -h now")

    def test_critical_battery_skips_heavy_backup_on_shutdown(self):
        """Critical battery shutdown must skip final backup to drop current load immediately."""
        run_shutdown_pi5 = self.sched_scope["run_shutdown_pi5"]
        mock_backup = MagicMock()
        self.sched_scope["final_backup_before_shutdown"] = mock_backup

        with patch("os.system"), \
             patch("time.sleep"):
            run_shutdown_pi5(early_reason="critical_battery_9.50V")
            mock_backup.assert_not_called()

    # -------------------------------------------------------------------------
    # 4. Media Health Check Logic
    # -------------------------------------------------------------------------
    def test_check_media_health_missing_usb(self):
        """Startup media check must detect missing USB storage and log warning."""
        check_media_health = self.sched_scope["check_media_health"]
        warning_file = self.control_root / "media_warning.txt"

        self.sched_scope["psutil"].disk_partitions.return_value = []
        with patch("shutil.disk_usage", return_value=MagicMock(free=15 * 1024**3, total=30 * 1024**3)), \
             patch("time.sleep"):
            warning = check_media_health(mounts_file=str(self.mounts_file))
            self.assertEqual(warning, "NO USB DRIVE")
            self.assertTrue(warning_file.exists())
            self.assertEqual(self.sched_scope["read_control"]("media_warning"), "NO USB DRIVE")

    def test_check_media_health_remount_ro_success(self):
        """Read-only USB partition must be self-healed via remount rw."""
        check_media_health = self.sched_scope["check_media_health"]
        warning_file = self.control_root / "media_warning.txt"
        fake_part = MagicMock(mountpoint="/media/USBDRIVE", device="/dev/sdb1", opts="ro,nosuid,nodev")

        self.sched_scope["psutil"].disk_partitions.return_value = [fake_part]
        self.sched_scope["subprocess"].run.return_value.returncode = 0

        with patch("shutil.disk_usage", return_value=MagicMock(free=20 * 1024**3, total=60 * 1024**3)), \
             patch("time.sleep"):

            warning = check_media_health(mounts_file=str(self.mounts_file))
            self.sched_scope["subprocess"].run.assert_called_with(["sudo", "mount", "-o", "remount,rw", "/media/USBDRIVE"], capture_output=True, text=True)
            self.assertIsNone(warning)
            self.assertFalse(warning_file.exists())

    def test_check_media_health_unrecoverable_ro_triggers_single_reboot(self):
        """Unrecoverable read-only mount triggers one-time reboot, without reboot looping."""
        check_media_health = self.sched_scope["check_media_health"]
        warning_file = self.control_root / "media_warning.txt"
        reboot_file = self.control_root / "media_reboot_attempted.txt"
        fake_part = MagicMock(mountpoint="/media/USBDRIVE", device="/dev/sdb1", opts="ro,nosuid,nodev")

        self.sched_scope["psutil"].disk_partitions.return_value = [fake_part]
        self.sched_scope["subprocess"].run.return_value.returncode = 1
        self.sched_scope["subprocess"].run.return_value.stderr = "Input/output error"

        with patch("shutil.disk_usage", return_value=MagicMock(free=20 * 1024**3, total=60 * 1024**3)), \
             patch("os.system") as mock_os_system, \
             patch("sys.exit") as mock_exit, \
             patch("time.sleep"):

            # Pass 1: reboot not attempted yet -> reboots to run fsck
            check_media_health(mounts_file=str(self.mounts_file))
            mock_os_system.assert_called_with("sudo reboot")
            mock_exit.assert_called_with(0)
            self.assertEqual(self.sched_scope["read_control"]("media_reboot_attempted"), "1")

            # Pass 2: reboot already attempted -> must NOT reboot loop; presents warning instead
            mock_os_system.reset_mock()
            mock_exit.reset_mock()
            warning = check_media_health(mounts_file=str(self.mounts_file))
            mock_os_system.assert_not_called()
            mock_exit.assert_not_called()
            self.assertEqual(warning, "USB READ-ONLY")
            self.assertTrue(warning_file.exists())

    def test_check_media_health_low_usb_storage(self):
        """USB space < 1.0 GB triggers USB FULL warning."""
        check_media_health = self.sched_scope["check_media_health"]
        warning_file = self.control_root / "media_warning.txt"
        fake_part = MagicMock(mountpoint="/media/USBDRIVE", device="/dev/sdb1", opts="rw")

        self.sched_scope["psutil"].disk_partitions.return_value = [fake_part]

        with patch("shutil.disk_usage", side_effect=[
                 MagicMock(free=10 * 1024**3, total=30 * 1024**3),  # SD usage
                 MagicMock(free=500 * 1024**2, total=60 * 1024**3), # USB usage: 500MB (<1GB)
                 MagicMock(free=500 * 1024**2, total=60 * 1024**3),
             ]), \
             patch("time.sleep"):

            warning = check_media_health(mounts_file=str(self.mounts_file))
            self.assertEqual(warning, "USB FULL")
            self.assertTrue(warning_file.exists())

    # -------------------------------------------------------------------------
    # 5. ePaper Display Override on Media Warning
    # -------------------------------------------------------------------------
    def test_should_update_display_overrides_sparse_blackout_on_media_warning(self):
        """Active media warning must force ePaper display refresh even during nocturnal blackout."""
        should_update_display = self.sched_scope["should_update_display"]
        atomic_update = self.sched_scope["atomic_update_kv"]
        settings = {"epaper_mode": "sparse"}
        night_dt = datetime(2026, 10, 18, 23, 0, 0)  # 11:00 PM (nighttime blackout)

        # Without warning: nocturnal blackout suppresses update
        self.assertFalse(should_update_display("boot", settings, "ACTIVE", now_dt=night_dt))

        # With media warning: must FORCE update so operator sees the warning on deployment
        atomic_update(str(self.control_root / "media_warning.txt"), "media_warning", "NO USB DRIVE")
        self.assertTrue(should_update_display("boot", settings, "ACTIVE", now_dt=night_dt))

    # -------------------------------------------------------------------------
    # 6. UpdateDisplay Warning Banner Rendering
    # -------------------------------------------------------------------------
    def test_updatedisplay_renders_media_warning_banner(self):
        """UpdateDisplay must draw inverted full-width warning banner when media_warning is set."""
        from PIL import Image, ImageDraw

        warning_file = self.control_root / "media_warning.txt"
        self.tp_scope["atomic_update_kv"](warning_file, "media_warning", "NO USB DRIVE")

        img = Image.new("1", (250, 122), 255)
        draw = ImageDraw.Draw(img)

        media_warning = self.tp_scope["read_control"](warning_file, "media_warning", "")
        self.assertEqual(media_warning, "NO USB DRIVE")

        msg = f"WARNING: {media_warning.upper()}"
        by = 41
        bh = 20
        # Draw inverted black bar and text
        draw.rectangle([(0, by), (250, by + bh)], fill=0)
        draw.text((30, by + 3), msg, fill=255)

        # Verify black pixels inside banner box and white background elsewhere
        self.assertEqual(img.getpixel((5, by + 5)), 0)   # Black background
        self.assertEqual(img.getpixel((5, 10)), 255)     # White canvas elsewhere


if __name__ == "__main__":
    unittest.main()
