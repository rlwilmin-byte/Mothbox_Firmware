# Remote Field Diagnostics CLI

This directory contains remote diagnostic and inquiry tools for inspecting live Mothbox hardware units in the field over SSH.

## Main Tool: `mothbox_remote.py`
A unified CLI tool replacing ad-hoc query and fix scripts.

### Common Commands:
```bash
# High-level system health, lockfile, and log tail check
python3 mothbox_remote.py status

# Inspect systemd journal (current boot or previous boot)
python3 mothbox_remote.py journal --boot 0 --lines 50
python3 mothbox_remote.py journal --boot -1 --unit cron

# Inspect power events, PMIC reset lines, and battery INA219
python3 mothbox_remote.py power

# Inspect cron configuration and recent execution history
python3 mothbox_remote.py cron

# Inspect runtime lockfiles and active processes
python3 mothbox_remote.py locks

# Inspect remote mothbox_settings.csv
python3 mothbox_remote.py config

# Inspect and synchronize RTC / timezone / fake-hwclock
python3 mothbox_remote.py clock --sync-rtc --disable-fake-hwclock

# Execute arbitrary command on the remote unit
python3 mothbox_remote.py cmd "tail -f /home/pi/Desktop/Mothbox/logs/Scheduler_log.txt"
```

Configuration can also be passed via environment variables:
- `MOTHBOX_HOST` (default: `10.42.0.34`)
- `MOTHBOX_USER` (default: `pi`)
- `MOTHBOX_PASS` (default: `luna`)
