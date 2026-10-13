# Mothbox Firmware Development Context & Guidelines

## 1. Project Background & Scope
This repository is a performance, cadence, and resilience fork of [Digital-Naturalism-Laboratories/Mothbox_Firmware](https://github.com/Digital-Naturalism-Laboratories/Mothbox_Firmware) maintained by `@rlwilmin-byte`.

### Core Goals:
1. **Hardened Uptime & True Duty Cycling:** Fix scheduler bugs that drop scheduled capture hours. **CRITICAL:** Do NOT fix sleep/wake bugs by forcing the Pi awake all night; the system relies on deep RTC halt (`POWER_OFF_ON_HALT=1`, drawing <0.02A vs >0.25A awake) to achieve 5–7+ days of autonomous field deployment.
2. **High-Temporal Capture (~5s interval):** Implement rapid interval (~5s) and burst capture modes using a persistent camera stream/daemon (Cron cannot run sub-minute).
3. **Alternative Video Capture Mode:** Add chunked MP4 video recording with continuous LED illumination control (`RAIL_12V` active).
4. **100% Backward-Compatible CSV Framework:** All new capabilities must be configurable via `mothbox_settings.csv` with safe fallback defaults that revert to stock behavior if keys are missing.
5. **Log Convention Parity:** All new tools and modes must log to the standard `/home/pi/Desktop/Mothbox/logs/` directory using identical banner formatting so that `Backup_Files.py` and diagnostic parsers ingest them seamlessly.

---

## 2. Empirical Findings from Field Run Analysis (2026-10-11/12 `webbedPavo`)
A field analysis of 12 scheduled hours (18:00–06:00) revealed an effective capture rate of only **33.5% (237 photos)** due to two distinct bugs in `Mothbox_Unified/Scheduler.py`:

### Bug A: The `BOOT_LOCK` Orphan Bug (Lost hours: 23:00, 03:00, 05:00)
- **Location:** `Mothbox_Unified/Scheduler.py` lines 988–1006 (`run_shutdown_pi5`) and 1095–1118 (`run_shutdown_pi5_FAST`).
- **Cause:** When a session runtime drifts slightly into the next scheduled hour, `should_abort_shutdown()` correctly aborts shutdown and reschedules it. However, `BOOT_LOCK` (`/run/boot_script_running`) is written *before* this check. When the function returns, `BOOT_LOCK` is **never removed**.
- **Effect:** `TakePhoto.py` exits immediately on line 28 if `BOOT_LOCK` exists. The Pi sat powered on for the entire rescheduled hour, taking 0 photos.
- **Fix:** In both shutdown functions, if shutdown is aborted, call `os.remove(BOOT_LOCK)` immediately before `return`.

### Bug B: The ePaper GPIO Reset Hang (Lost hours: 18:00, 19:00, 20:00, 01:00)
- **Location:** `Mothbox_Unified/Scheduler.py` line 2223.
- **Cause:** `GPIO.cleanup()` is executed immediately before invoking `run_helper(".../UpdateDisplay.py")`. This drops the MOSFET enable pins (3.3V and 12V rails) and disrupts the SPI bus on Pi 5 Bookworm, causing an unhandled hardware hang/PMIC reset during display refresh.
- **Fix:** Remove `GPIO.cleanup()` prior to `UpdateDisplay.py`, and provide an option to bypass nighttime ePaper updates (`enable_epaper: 0`).

---

## 3. Hardware Architecture Reference (Mothbox PRO PCB v5.x)
- **Host:** Raspberry Pi 5 Model B Rev 1.1 with Bookworm (kernel 6.x)
- **Camera:** Arducam 64MP OwlSight (OV64A40 via PiSP)
- **Sensors:** INA219 (voltage/current @ 0x40), LTR-303 (lux @ 0x29) behind 3.3V switched rail
- **GPIO Pin Map:**
  - `CH1=5, CH2=6, CH3=9, CHEXT=22` (Attractor light MOSFETs, active HIGH)
  - `FLASH=19` (Flash pulse MOSFET, active HIGH)
  - `RAIL_12V=23` (12V MOSFET Q8 enable, active HIGH — drives lights)
  - `RAIL_3V3=27` (3.3V MOSFET Q11 enable, active LOW — drives sensors + DIP switch expanders)
  - Waveshare ePaper 2.13in: SPI0 (`MOSI=10, SCLK=11, CS=8`), `DC=25, RST=17, BUSY=24`

---

## 4. Live Testing Workflow (SSH over Wi-Fi)
When testing on a live Mothbox:
1. Ensure Mothbox has its **Debug** switch flipped to `1` (or `mode=DEBUG` in CSV).
2. Connect to the Mothbox via SSH (`pi@<mothbox-ip>`).
3. Changes can be pushed via git or tested directly in place.

---

## Execution & Token Safeguards
- **No Autonomous File Sweeps:** Never recursively read files across the repository without asking first. Look only at specifically referenced paths.
- **Subagent Boundaries:** Do not spawn background worker subagents or multi-step execution loops autonomously. If a task requires more than 3 steps, present an action plan for approval first.
- **Fail Fast:** If a terminal command, test, or script fails twice in a row, STOP immediately. Do not attempt speculative auto-fixes or retry loops. Explain the error and await instructions.
- **No Large File Dumps:** Never read data logs, raw binary files, or tables directly into context. Summarize using head/tail commands if inspection is required.
