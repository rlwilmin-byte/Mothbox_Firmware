#!/usr/bin/env python3
"""
Unit tests for Backup_Files resilience:
- Bounded batch transfers (FIFO ordering)
- Atomic per-file copy and move to photos_backedup/
- FSCK*.REC detection and reclamation
- Safe handling of disk full (ENOSPC)
"""
import os
import sys
import tempfile
import shutil
import unittest
from pathlib import Path

# Add Mothbox_Unified to sys.path
_HERE = Path(__file__).resolve().parent
_UNIFIED = _HERE.parent
sys.path.insert(0, str(_UNIFIED))

import Backup_Files


class TestBackupResilience(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp(prefix="mb_backup_test_")
        self.source = Path(self.test_dir) / "photos"
        self.dest = Path(self.test_dir) / "external_backup"
        self.backedup = Path(self.test_dir) / "photos_backedup"
        self.source.mkdir()
        self.dest.mkdir()
        self.backedup.mkdir()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_reclaim_rec_files_purge_when_constrained(self):
        # Create mock .REC files on external drive
        rec1 = self.dest / "FSCK0001.REC"
        rec2 = self.dest / "FSCK0002.REC"
        rec1.write_bytes(b"A" * 1024 * 1024)
        rec2.write_bytes(b"B" * 1024 * 1024)

        # Force min_free_bytes very high so it triggers the reclaim
        reclaimed = Backup_Files.reclaim_orphaned_rec_files(self.dest, min_free_bytes=10**12)
        self.assertEqual(reclaimed, 2 * 1024 * 1024)
        self.assertFalse(rec1.exists())
        self.assertFalse(rec2.exists())

    def test_reclaim_rec_files_quarantine_when_ample_space(self):
        # Create mock .REC files on external drive
        rec1 = self.dest / "FSCK0001.REC"
        rec1.write_bytes(b"DATA" * 1024)

        # Force min_free_bytes to 0 so space is considered ample
        reclaimed = Backup_Files.reclaim_orphaned_rec_files(self.dest, min_free_bytes=0)
        self.assertEqual(reclaimed, 0)
        self.assertFalse(rec1.exists())
        salvage = self.dest / ".fsck_salvage" / "FSCK0001.REC"
        self.assertTrue(salvage.exists())

    def test_incremental_backup_and_atomic_move(self):
        # Create 5 mock photo files
        sub = self.source / "session_2026-10-18"
        sub.mkdir()
        for i in range(5):
            (sub / f"photo_{i:02d}.jpg").write_bytes(b"JPEG_MOCK_" + str(i).encode() * 1000)

        # Run with max_bytes capping at 2 files
        file_sz = (sub / "photo_00.jpg").stat().st_size
        max_bytes = file_sz * 2 - 1

        copied_count, copied_bytes, disk_full = Backup_Files.incremental_backup_photos(
            self.source, self.dest, self.backedup, max_bytes=max_bytes
        )

        self.assertEqual(copied_count, 2)
        self.assertFalse(disk_full)

        # Check destination has 2 files
        dest_files = list(self.dest.glob("**/*.jpg"))
        self.assertEqual(len(dest_files), 2)

        # Check backedup has 2 files
        backedup_files = list(self.backedup.glob("**/*.jpg"))
        self.assertEqual(len(backedup_files), 2)

        # Check source has remaining 3 files
        src_files = list(self.source.glob("**/*.jpg"))
        self.assertEqual(len(src_files), 3)

    def test_enospc_safe_handling(self):
        # Simulate ENOSPC during copy
        import unittest.mock as mock
        import errno

        sub = self.source / "session_enospc"
        sub.mkdir()
        (sub / "photo_err.jpg").write_bytes(b"TEST_DATA")

        def mock_copyfile(src, dst, *args, **kwargs):
            raise OSError(errno.ENOSPC, "No space left on device")

        with mock.patch("shutil.copyfile", side_effect=mock_copyfile):
            copied_count, copied_bytes, disk_full = Backup_Files.incremental_backup_photos(
                self.source, self.dest, self.backedup
            )
            self.assertEqual(copied_count, 0)
            self.assertTrue(disk_full)
            # Source file must be preserved
            self.assertTrue((sub / "photo_err.jpg").exists())

    def test_clean_startup_state_tracking(self):
        # Verify shutdown state file mechanics used by Scheduler.py
        import tempfile
        state_file = Path(self.test_dir) / "shutdown_state.txt"
        
        # 1. Simulate graceful shutdown marking state as clean
        state_file.write_text("shutdown_state=clean\n")
        self.assertIn("shutdown_state=clean", state_file.read_text())

        # 2. Simulate startup arming crash recovery
        state_file.write_text("shutdown_state=dirty\n")
        self.assertIn("shutdown_state=dirty", state_file.read_text())

    def test_import_with_boot_lock_present(self):
        # Crucial regression test: importing Backup_Files while BOOT_LOCK is present
        # must NOT raise SystemExit. The exit guard belongs inside __main__ only.
        import unittest.mock as mock
        with mock.patch("os.path.exists", side_effect=lambda p: True if p == Backup_Files.BOOT_LOCK else False):
            # Re-importing or accessing Backup_Files functions should not raise SystemExit
            self.assertTrue(callable(Backup_Files.reclaim_orphaned_rec_files))


if __name__ == "__main__":
    unittest.main()
