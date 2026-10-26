# Forensic Investigation Report: SD 2 (`wittyBee`)

**Investigation Date:** 2026-10-26  
**Target Hardware:** Mothbox PRO v5.x (Raspberry Pi 5 Model B Rev 1.1, Serial: `4f31ab9dada89797`)  
**Deployment Window:** 2026-10-24 19:00 to 2026-10-25 06:00 (AEDT, UTC+11)  
**Settings Profile:** Schedule B (`hour,19;20;21;22;23;0;1;2;3;4;5`), `runtime,55`, `capture_mode,burst` (8 photos @ 6.0s spacing every 1 min), `epaper_mode,sparse`, `hardware,pro`

---

## 1. Executive Summary & Yield

| Metric | Scheduled / Target | Actual Recorded | Yield Rate | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Active Capture Hours** | 11 hours (19:00–05:00) | 5 hours (4 full, 1 partial) | **45.5%** | FAILED (6 hours dropped) |
| **Photos Captured** | 4,400 photos (400/hr) | **1,656 photos** | **37.6%** | FAILED |
| **Clean Shutdowns** | 11 | 4 | 36.4% | Severe Brownout at 02:09 |
| **Flash Reliability** | 4,400 pulses | 1,657 pulses | - | Max Sag: **-2.84V (to 8.57V)** |

---

## 2. Chronological Hour-by-Hour Forensic Timeline

| Scheduled Hour | Boot Time | Actual Photos | Session Status | Peak Sag / Voltage | Failure Mode / Observations |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **19:00** | *Did not boot* | **0** | **SKIPPED** | - | Pi remained off following ungraceful pre-deployment power disconnect at 14:28. |
| **20:00** | 20:00:24 | **400** | Clean (55m) | 11.67V → 9.20V (-2.47V) | Perfect burst execution (50 bursts × 8). Clean RTC set for 21:00. |
| **21:00** | 21:00:21 | **400** | Clean (55m) | 12.10V → 9.55V (-2.55V) | Perfect burst execution. Clean RTC set for 22:00. |
| **22:00** | 22:00:22 | **400** | Clean (55m) | 11.97V → 9.38V (-2.59V) | Alert forced ePaper update at boot despite night blackout. Clean RTC for 23:00. |
| **23:00** | 23:00:22 | **0** | **CRASH (t=1s)** | - | **CRASH AT STARTUP:** `Scheduler.py` crashed during hardware detection / I2C scan. Next wakealarm was **NEVER SET**. |
| **00:00** | 00:00:25 | **400** | Clean (55m) | 11.81V → 9.15V (-2.66V) | Woke via lingering scheduled alarm. Perfect burst execution. Clean RTC set for 01:00. |
| **01:00** | 01:00:21 | **0** | **CRASH (t=3s)** | - | **CRASH AT STARTUP:** `Scheduler.py` crashed at `[ CONFIGURATION -- Scheduling ]` before RTC alarm was armed. |
| **02:00** | 02:00:24 | **56** | **CRASH (t=9m)** | 11.41V → **8.57V (-2.84V)** | Woke at 02:00:24. Took 7 bursts (56 photos). On burst 8 frame 1, flash sag dropped 12V rail to 8.57V, causing **PMIC Brownout Reset**. |
| **03:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. Brownout corrupted state; wakealarm not armed. |
| **04:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. |
| **05:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. |

---

## 3. Root Cause Analysis

### Failure Mode 1: Heavy Dynamic Flash Sag & PMIC Brownout Collapse (02:09 Crash)
- **Observed Behavior:** During early hours (20:00–22:00), battery was fresh (12.1V–11.8V) and flash sag dipped to ~9.2V–9.5V, within operating margins. By 02:00, battery voltage had dropped to ~11.3V idle. Under 8-photo burst cycles with ~1.4s flash pulse duration, the 12V rail sagged by **-2.84V**, hitting **8.57V**.
- **Hardware Consequence:** The Pi 5 buck converter dropped out under the dynamic load surge, causing a transient dip on the 5V power plane and an immediate PMIC hardware shutdown.
- **Timing Parity Across Fleet:** `wittyBee` collapsed at **02:09:02**, `jumpChucla` collapsed at **02:09:48**, and `webbedPavo` collapsed at **02:10:01**. All three boxes suffered identical battery discharge curves and brownout resets at the exact same point in the deployment!

### Failure Mode 2: Unhandled Exceptions in Early Startup Blocks (23:00 & 01:00 Crashes)
- **Observed Behavior:** In Boot #11 (23:00:22), execution halted after `EEPROM settings verified correct.` In Boot #13 (01:00:21), execution halted at `[ CONFIGURATION -- Scheduling ]`.
- **Root Cause:** Transient I2C sensor bus timeouts and NTP synchronization exceptions occurring before `[ CONFIGURATION -- Scheduling ]` without outer `try...except` safety envelopes kill `Scheduler.py` abruptly. When `Scheduler.py` dies before arming the next RTC wake alarm, the system loses autonomous recovery.

### Failure Mode 3: Warning Alerts Overriding Night ePaper Blackout
- `wittyBee` lacked an external USB drive, triggering `WARNING: NO USB DRIVE`. This alert forced ePaper display updates at 20:00, 21:00, 22:00, and 02:00, wasting power and inducing thermal/current strain on the battery.

---

## 4. Evidence Preserved in Workspace

Archived in `Mothbox_Firmware/forensics/sd2_wittyBee/`:
- `logs/Scheduler_log.txt`: All 14 boot sessions, shutdowns, and crashes.
- `logs/TakePhoto_log.txt`: 3,344 photo records and flash sag measurements.
- `logs/Diagnostics*.log`: Hardware and sensor telemetry across boots.
- `mothbox_settings.csv` & `bootfs_config.txt`.
- `journal/`: Systemd binary journal.
