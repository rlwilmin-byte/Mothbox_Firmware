# Field Diagnostics & Remote Query Scripts

This directory contains diagnostic inquiry and utility scripts developed during live field testing (e.g. over SSH to test hardware units such as `jumpchula`).

## Contents:
- **`query_*.py`**: Scripts querying systemd journals, boot sequences, cron execution histories, PMIC reset lines, and lockfile states over SSH via `pexpect`.
- **`disable_fake_hwclock.py` / `fix_jumpchula_tz.py` / `verify_tz_jumpchula.py`**: Utilities for synchronizing RTC and hardware clocks on remote field test units.
