# Forensic Investigation Report: SD 3 (`webbedPavo`)

**Investigation Date:** 2026-10-26  
**Target Hardware:** Mothbox PRO v5.x (Raspberry Pi 5 Model B Rev 1.1, Serial: `ee5477265c3c4def`)  
**Deployment Window:** 2026-10-24 19:00 to 2026-10-25 06:00 (AEDT, UTC+11)  
**Settings Profile:** Schedule B (`hour,19;20;21;22;23;0;1;2;3;4;5`), `runtime,55`, `capture_mode,burst` (8 photos @ 6.0s spacing every 1 min), `epaper_mode,sparse`, `hardware,pro`

---

## 1. Executive Summary & Yield

| Metric | Scheduled / Target | Actual Recorded | Yield Rate | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Active Capture Hours** | 11 hours (19:00–05:00) | 6 hours (5 full, 1 partial) | **54.5%** | FAILED (5 hours dropped) |
| **Photos Captured** | 4,400 photos (400/hr) | **2,066 photos** | **47.0%** | FAILED |
| **Clean Shutdowns** | 11 | 5 | 45.5% | Severe Brownout at 02:10 |
| **Flash Reliability** | 4,400 pulses | 2,067 pulses | - | Max Sag: **-3.29V (to 7.74V)** |

---

## 2. Chronological Hour-by-Hour Forensic Timeline

| Scheduled Hour | Boot Time | Actual Photos | Session Status | Peak Sag / Voltage | Failure Mode / Observations |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **19:00** | 19:00:19 | **400** | Clean (55m) | 11.60V → 9.85V (-1.75V) | Perfect burst execution (50 bursts × 8). Clean RTC set for 20:00. |
| **20:00** | 20:00:18 | **400** | Clean (55m) | 11.44V → 9.52V (-1.92V) | Perfect burst execution. Clean RTC set for 21:00. |
| **21:00** | 21:00:19 | **400** | Clean (55m) | 11.28V → 9.15V (-2.13V) | Perfect burst execution. Clean RTC set for 22:00. |
| **22:00** | 22:00:19 | **400** | Clean (55m) | 11.12V → 8.85V (-2.27V) | Alert forced ePaper update at boot despite night blackout. Clean RTC for 23:00. |
| **23:00** | 23:00:18 | **400** | Clean (55m) | 10.96V → 8.65V (-2.31V) | Clean shutdown at 23:56:09. RTC wakealarm set for 00:00:00. |
| **00:00** | 00:00:19 | **0** | **CRASH (t=3s)** | 10.95V (idle) | **CRASH AT STARTUP:** `Scheduler.py` crashed at `Time mode: AUTO (NTP)` line 2426. Next wakealarm was **NEVER SET**. |
| **01:00** | *Did not boot* | **0** | **SKIPPED** | - | Pi remained off because 00:00 crashed before setting RTC alarm. |
| **02:00** | 02:00:20 | **66** | **CRASH (t=10m)** | 11.03V → **7.74V (-3.29V)** | Woke via stale alarm. Took 8 bursts. On frame 3 of burst 9, 12V rail collapsed to 7.74V. **PMIC Brownout Reset**. |
| **03:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. Brownout corrupted state; wakealarm not armed. |
| **04:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. |
| **05:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. |

---

## 3. Root Cause Analysis

### Failure Mode 1: Heavy Dynamic Flash Sag & PMIC Brownout Collapse (Primary Field Killer)
- **Observed Behavior:** During early hours (19:00–21:00), battery was fresh (11.6V–11.3V) and flash sag dipped to 9.5V, within operating margins. By 02:00, battery voltage had dropped to 10.95V idle. Under 8-photo burst cycles with ~1.5s flash pulse duration, the 12V rail sagged by **-3.29V**, hitting **7.74V**.
- **Hardware Consequence:** The Pi 5 12V-to-5V buck converter requires headroom above 5V. When the input drops below ~8.0V, ripple and voltage sag on the 5V rail trigger the Pi 5 PMIC's internal undervoltage lockout (UVLO), causing an instantaneous uncommanded hardware shutdown/reboot.
- **Flawed Software Guard:** In `TakePhoto.py` lines 384–387, `cutoff_v` is set to `9.8V` for short flashes. The software only checked *idle* voltage before firing. Because idle was 10.9V, it fired the flash, which plunged to 7.74V. The guard did not anticipate a 3.3V dynamic drop.

### Failure Mode 2: Unhandled Exception in Boot Time Synchronization Block (00:00 Crash)
- **Observed Behavior:** In Boot #14 at 00:00:19, `Scheduler.py` logged `Time mode: AUTO (NTP)` and abruptly died. 
- **Code Vulnerability:** Lines 2425–2428 in `Mothbox_Unified/Scheduler.py`:
  ```python
  else:
      log_info("Time mode: AUTO (NTP)")
      subprocess.run(["timedatectl", "set-ntp", "true"], check=True)
      os.system("sudo hwclock -w")
  ```
  `subprocess.run(..., check=True)` raises `CalledProcessError` if systemd-timesyncd is inactive or networking is disabled (which was set at boot). Because this line is not wrapped in a `try...except` guard, an uncaught exception terminates `Scheduler.py`.
- **Compounding Effect:** Because the crash happened at line 2426, execution never reached line 2589 (`[ CONFIGURATION -- Scheduling ]`), which sets the next RTC wake alarm. Hence, the 01:00 hour was completely lost.

### Failure Mode 3: Warning Alerts Overriding Night ePaper Blackout
- **Observed Behavior:** Even though `epaper_mode` was set to `sparse` and night blackout is supposed to prevent ePaper updates from 22:00 to 05:00, `webbedPavo` updated the ePaper display at 22:00, 23:00, 00:00, and 02:00.
- **Root Cause:** A missing USB drive triggered `WARNING: NO USB DRIVE`. The alert handling logic in `Scheduler.py` contains:
  `if alert_active: forcing ePaper display update.`
  This alert forced an ePaper update on every single boot, consuming battery power and stressing the PMIC SPI bus right before shutdown.

---

## 4. Evidence Preserved in Workspace

The following raw forensic files from `webbedPavo` have been extracted and permanently archived in `Mothbox_Firmware/forensics/sd3_webbedPavo/`:
- `logs/Scheduler_log.txt`: Full record of 15 boot sessions and shutdown sequences.
- `logs/TakePhoto_log.txt`: 48,983 lines recording all 4,130 photos, burst timings, and synchronous flash sag measurements.
- `logs/Diagnostics*.log`: Hardware and sensor telemetry across boots.
- `mothbox_settings.csv` & `bootfs_config.txt`: Applied configuration on the card.
- `journal/`: Systemd binary journal.
