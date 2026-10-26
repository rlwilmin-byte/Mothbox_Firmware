# Forensic Investigation Report: SD 1 (`jumpChucla`)

**Investigation Date:** 2026-10-26  
**Target Hardware:** Mothbox PRO v5.x (Raspberry Pi 5 Model B Rev 1.1, Serial: `c645245f21a2814b`)  
**Deployment Window:** 2026-10-24 19:00 to 2026-10-25 06:00 (AEDT, UTC+11)  
**Settings Profile:** Schedule B (`hour,19;20;21;22;23;0;1;2;3;4;5`), `runtime,55`, `capture_mode,burst` (8 photos @ 6.0s spacing every 1 min), `epaper_mode,sparse`, `hardware,pro`

---

## 1. Executive Summary & Yield

| Metric | Scheduled / Target | Actual Recorded | Yield Rate | Status |
| :--- | :--- | :--- | :--- | :--- |
| **Active Capture Hours** | 11 hours (19:00–05:00) | 1 partial hour (02:00) | **9.1%** | CATASTROPHIC FAILURE (10 hours dropped) |
| **Photos Captured** | 4,400 photos (400/hr) | **61 photos** | **1.4%** | CATASTROPHIC FAILURE |
| **Clean Shutdowns** | 11 | 0 | **0.0%** | 100% Dirty Crashes |
| **Flash Reliability** | 4,400 pulses | 61 pulses | - | Average Flash Sag: **-2.68V (to 9.32V)** |

---

## 2. Chronological Hour-by-Hour Forensic Timeline

| Scheduled Hour | Boot Time | Actual Photos | Session Status | Peak Sag / Voltage | Failure Mode / Observations |
| :---: | :---: | :---: | :---: | :---: | :--- |
| **Pre-Deploy** | 14:26:47 | - | **CRASH (t=2s)** | - | **CRASH AT STARTUP:** `Scheduler.py` crashed at `STARTUP -- Identity` / `GetConfigSwitches.py`. Next wakealarm for 19:00 was **NEVER ARMED**. |
| **19:00** | *Did not boot* | **0** | **SKIPPED** | - | Pi remained off because pre-deployment boot crashed before arming RTC. |
| **20:00** | *Did not boot* | **0** | **SKIPPED** | - | Pi remained off. |
| **21:00** | 21:00:19 | **0** | **HARD HANG (t=15s)** | 11.57V (idle) | **EPAPER BUS HANG:** Booted at 21:00. At `LAUNCH -- Display`, `mothbox_hw.rail_3v3(False)` dropped the 3.3V rail immediately before calling `UpdateDisplay.py`. The ePaper SPI bus hard-locked the Pi. `TakePhoto.py` **NEVER RAN**. Boot lock remained orphaned. |
| **22:00** | *Did not boot* | **0** | **SKIPPED** | - | System remained frozen/locked from 21:00. |
| **23:00** | *Did not boot* | **0** | **SKIPPED** | - | System remained locked. |
| **00:00** | *Did not boot* | **0** | **SKIPPED** | - | System remained locked. |
| **01:00** | *Did not boot* | **0** | **SKIPPED** | - | System remained locked. |
| **02:00** | 02:00:19 | **61** | **CRASH (t=9m)** | 12.05V → **9.32V (-2.73V)** | Woke at 02:00:19. Because 02:00 was in blackout (22:00–05:00), ePaper update was skipped! Session ran, took 61 burst photos. On burst 8 frame 6, flash sag caused **PMIC Brownout Reset**. |
| **03:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. Brownout corrupted state; wakealarm not armed. |
| **04:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. |
| **05:00** | *Did not boot* | **0** | **SKIPPED** | - | System dead. |

---

## 3. Root Cause Analysis

### Failure Mode 1: The 3.3V Rail Drop & ePaper SPI Hardware Hang (21:00 Hour Killer)
- **Observed Behavior:** In Boot #4 at 21:00:19, `Scheduler.py` reached `LAUNCH -- Display`. It printed `Quiescent delay (5s) for power rail and PMIC settling prior to ePaper update... Updating ePaper display (if available)...` and then completely halted.
- **Root Cause Code:** Lines 2788–2794 of `Scheduler.py`:
  ```python
  if mothbox_hw is not None:
      try:
          mothbox_hw.attract_off()
          mothbox_hw.rail_12v(False)
          mothbox_hw.rail_3v3(False)  # <-- DROPS 3.3V SENSOR & EXPANDER RAIL
      except Exception:
          pass
  ```
  Calling `rail_3v3(False)` de-energizes the 3.3V rail. When `UpdateDisplay.py` launches, it attempts to read the INA219 sensor at `0x40` and initialize the Waveshare ePaper SPI interface. On Pi 5 Bookworm, cutting the 3.3V rail before display initialization triggers an unhandled SPI/I2C kernel hang, freezing the Pi in a high-current awake state with the boot lock active.
- **Why 02:00 Worked When 21:00 Failed:** At 02:00, `is_deep_night` was `True` (22:00–05:00 blackout), so `should_update_display` returned `False` and skipped the ePaper code path entirely! This allowed 02:00 to proceed directly into `TakePhoto.py`.

### Failure Mode 2: Flash Current Sag Brownout
- **Observed Behavior:** At 02:00, when `TakePhoto.py` executed burst mode, each flash pulse lasted 1.2s to 1.5s.
- **Electrical Measurements:**
  - Idle Voltage: ~12.04V
  - Under Flash: ~9.32V
  - Dynamic Sag: **-2.73V**
  - Current: **0.518A**
- After 7 complete bursts, cumulative heating and battery discharge on burst 8 frame 6 caused a transient dropout on the Pi 5 5V rail, triggering a PMIC hardware shutdown.

### Failure Mode 3: Pre-Deployment Crash in `GetConfigSwitches.py`
- In Boot #3 at 14:26:47, execution stopped immediately after `Device name (auto): jumpChucla`. Line 2502 calls `GetConfigSwitches.py`. Any failure communicating with the MCP23017/PCA9555 DIP expanders crashes `Scheduler.py` before the scheduling block, leaving the RTC alarm unprogrammed.

---

## 4. Evidence Preserved in Workspace

Archived in `Mothbox_Firmware/forensics/sd1_jumpChucla/`:
- `logs/Scheduler_log.txt`: All 5 boot sessions and crashes.
- `logs/TakePhoto_log.txt`: The 61 burst photos and flash sag logs.
- `logs/Diagnostics*.log`: Hardware telemetry across boots.
- `mothbox_settings.csv` & `bootfs_config.txt`.
- `journal/`: Systemd binary journal.
