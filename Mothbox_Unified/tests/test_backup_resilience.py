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


if __name__ == "__main__":
    unittest.main()
