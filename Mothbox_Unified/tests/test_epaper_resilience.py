#!/usr/bin/env python3
"""
Unit tests for ePaper Sparse Mode Resiliency & Brownout Protection:
- Default epaper_mode is 'sparse' (safe fallback)
- Strict nocturnal blackout (18:00 - 06:00) during sparse mode
- Dawn morning summary update (06:00 shutdown)
- Physical operator modes (DEBUG, PARTY, OFF) always refresh
- Low-voltage brownout safety gate (<9.8V PMIC floor)
- Avoidance of over-cautious cutoffs (10.0V - 11.0V allow updates)
- Invariant: Zero GPIO.cleanup() in firmware execution paths
"""

import ast
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
_UNIFIED = _HERE.parent
sys.path.insert(0, str(_UNIFIED))


def load_scheduler_epaper_helpers():
    """Extracts epaper helper functions and constants from Scheduler.py via AST."""
    with open(_UNIFIED / "Scheduler.py", "r", encoding="utf-8") as f:
        scheduler_code = f.read()
    tree = ast.parse(scheduler_code)
    nodes_to_exec = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SETTING_DEFAULTS" for t in node.targets):
            nodes_to_exec.append(node)
        elif isinstance(node, ast.FunctionDef) and node.name in ("get_epaper_mode", "should_update_display"):
            nodes_to_exec.append(node)

    mod = ast.Module(body=nodes_to_exec, type_ignores=[])
    compiled = compile(mod, filename="<ast>", mode="exec")
    scope = {
        "datetime": datetime,
        "time": MagicMock(time=lambda: 1000000.0),
        "log_warn": lambda *args: None,
        "log_info": lambda *args: None,
        "mothbox_hw": None,
        "read_control": lambda name, default="0": default,
    }
    exec(compiled, scope)
    return scope


class TestEpaperResilience(unittest.TestCase):
    def setUp(self):
        self.scope = load_scheduler_epaper_helpers()
        self.get_epaper_mode = self.scope["get_epaper_mode"]
        self.should_update_display = self.scope["should_update_display"]
        self.setting_defaults = self.scope["SETTING_DEFAULTS"]

    def test_default_epaper_mode_is_sparse(self):
        """Firmware defaults must specify sparse mode to eliminate nighttime brownouts."""
        self.assertEqual(self.setting_defaults.get("epaper_mode"), "sparse")
        self.assertEqual(self.get_epaper_mode({}), "sparse")
        self.assertEqual(self.get_epaper_mode(None), "sparse")
        self.assertEqual(self.get_epaper_mode({"photo_interval": 1}), "sparse")

    def test_epaper_mode_parsing(self):
        """Verify normalization of epaper_mode strings and legacy enable_epaper keys."""
        self.assertEqual(self.get_epaper_mode({"epaper_mode": "always"}), "always")
        self.assertEqual(self.get_epaper_mode({"enable_epaper": "1"}), "always")
        self.assertEqual(self.get_epaper_mode({"epaper_mode": "never"}), "never")
        self.assertEqual(self.get_epaper_mode({"enable_epaper": "0"}), "never")
        self.assertEqual(self.get_epaper_mode({"epaper_mode": "sparse"}), "sparse")

    def test_operator_modes_always_update(self):
        """Physical switch modes (DEBUG, PARTY, OFF) must always update display for operators."""
        settings = {"epaper_mode": "sparse"}
        # Even during the middle of the night (e.g. 02:00), operator modes must refresh
        night_dt = datetime(2026, 10, 18, 2, 0, 0)
        self.assertTrue(self.should_update_display("boot", settings, "DEBUG", now_dt=night_dt))
        self.assertTrue(self.should_update_display("boot", settings, "PARTY", now_dt=night_dt))
        self.assertTrue(self.should_update_display("boot", settings, "OFF", now_dt=night_dt))

    def test_nocturnal_blackout_in_sparse_mode(self):
        """Sparse mode suppresses ePaper updates on nocturnal shutdown, but boot/wake ranks higher to allow deployment."""
        settings = {"epaper_mode": "sparse"}

        night_hours = [18, 19, 21, 23, 0, 1, 3, 5]
        for hr in night_hours:
            dt = datetime(2026, 10, 18, hr, 15, 0)
            # Boot during night: always allowed so deployments at any hour get feedback!
            self.assertTrue(
                self.should_update_display("boot", settings, "ACTIVE", now_dt=dt),
                f"Failed: ePaper boot update blocked at {hr}:15 in sparse mode"
            )
            # Hourly shutdown during night: locked out to eliminate brownouts & save battery
            self.assertFalse(
                self.should_update_display("shutdown", settings, "ACTIVE", next_epoch=1000000 + 3600, now_dt=dt),
                f"Failed: ePaper updated on hourly shutdown at {hr}:15 during nocturnal blackout"
            )

    def test_dawn_summary_update_in_sparse_mode(self):
        """At dawn / end-of-night shutdown (sleep > 2h), sparse mode must update the display with the night's summary."""
        settings = {"epaper_mode": "sparse"}
        dawn_dt = datetime(2026, 10, 19, 5, 0, 0)  # 05:00 AM (end of night session)
        
        # Shutdown at end of night with next epoch 13h away (18:00 tonight, sleep > 2 hours)
        now_epoch = 1000000.0
        next_epoch = now_epoch + 13 * 3600  # 13 hours later
        self.scope["time"].time = lambda: now_epoch

        self.assertTrue(
            self.should_update_display("shutdown", settings, "ACTIVE", next_epoch=next_epoch, now_dt=dawn_dt)
        )

    def test_daytime_boot_in_sparse_mode(self):
        """Daytime boot (06:00 - 18:00) always updates display on wake and shutdown to give operators feedback."""
        settings = {"epaper_mode": "sparse"}
        day_dt = datetime(2026, 10, 19, 14, 0, 0)  # 2:00 PM

        now_epoch = 1000000.0
        self.scope["time"].time = lambda: now_epoch

        # Wake / boot in daytime gives immediate operator feedback
        self.assertTrue(self.should_update_display("boot", settings, "ACTIVE", now_dt=day_dt))

        # Shutdown / sleep in daytime gives immediate operator feedback
        self.assertTrue(self.should_update_display("shutdown", settings, "ACTIVE", now_dt=day_dt))

    def test_low_voltage_brownout_protection(self):
        """Battery < 9.8V must suppress ePaper update even in 'always' mode."""
        settings = {"epaper_mode": "always"}
        mock_hw = MagicMock()

        # Critically low battery (9.5V < 9.8V) -> SUPPRESS
        mock_hw.read_power.return_value = {"voltage_v": 9.5}
        self.scope["mothbox_hw"] = mock_hw
        self.assertFalse(self.should_update_display("boot", settings, "ACTIVE"))

        # Safe operational voltages (10.2V, 10.5V, 11.2V) -> ALLOW (not over-cautious)
        mock_hw.read_power.return_value = {"voltage_v": 10.2}
        self.assertTrue(self.should_update_display("boot", settings, "ACTIVE"))

        mock_hw.read_power.return_value = {"voltage_v": 10.8}
        self.assertTrue(self.should_update_display("boot", settings, "ACTIVE"))

    def test_no_gpio_cleanup_in_firmware_paths(self):
        """Verify that GPIO.cleanup() is absent from active firmware scripts to prevent pin float."""
        for fname in ["Scheduler.py", "TakePhoto.py", "Backup_Files.py", "UpdateDisplay.py", "mothbox_hw.py"]:
            with open(_UNIFIED / fname, "r", encoding="utf-8") as f:
                content = f.read()
            # Only epdconfig third-party lib or commented code should mention cleanup
            lines = [l.strip() for l in content.splitlines() if not l.strip().startswith("#")]
            cleanup_lines = [l for l in lines if "GPIO.cleanup(" in l and "def " not in l]
            # In UpdateDisplay.py, only KeyboardInterrupt exit should have cleanup
            if fname == "UpdateDisplay.py":
                self.assertEqual(len(cleanup_lines), 0)
            else:
                self.assertEqual(len(cleanup_lines), 0, f"Found unexpected GPIO.cleanup() in {fname}: {cleanup_lines}")


if __name__ == "__main__":
    unittest.main()
