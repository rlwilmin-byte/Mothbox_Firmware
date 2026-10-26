# Mothbox Fleet Forensic Investigation: Cross-Device Synthesis & Root Cause Analysis

**Investigation Date:** 2026-10-26  
**Deployment Window:** 2026-10-24 19:00 to 2026-10-25 06:00 (AEDT, UTC+11)  
**Fleet Devices Examined:**
1. **SD 1 (`jumpChucla`):** 128GB SanDisk Ultra, RPi 5 Rev 1.1 (Serial: `c645245f21a2814b`)
2. **SD 2 (`wittyBee`):** 1TB SanDisk Extreme, RPi 5 Rev 1.1 (Serial: `4f31ab9dada89797`)
3. **SD 3 (`webbedPavo`):** 256GB SanDisk Ultra, RPi 5 Rev 1.1 (Serial: `ee5477265c3c4def`)

---

## 1. Fleet-Wide Performance & Yield Comparison

| Device | Scheduled Hours | Active Hours Captured | Total Photos | Yield Rate | Clean Shutdowns | Primary Failure Mode |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **`jumpChucla` (SD 1)** | 11 | 1 partial (02:00) | **61** | **1.4%** | 0 / 11 (0%) | 3.3V rail drop ePaper hang @ 21:00; Flash brownout @ 02:09 |
| **`wittyBee` (SD 2)** | 11 | 5 (4 full, 1 partial) | **1,656** | **37.6%** | 4 / 11 (36.4%) | Early startup crashes @ 23:00 & 01:00; Flash brownout @ 02:09 |
| **`webbedPavo` (SD 3)** | 11 | 6 (5 full, 1 partial) | **2,066** | **47.0%** | 5 / 11 (45.5%) | NTP sync crash @ 00:00; Flash brownout @ 02:10 |
| **TOTAL FLEET** | **33 hrs** | **12 hrs (36.4%)** | **3,783** | **28.7%** | **9 / 33 (27.3%)** | **Fleet-wide brownout at 02:09–02:10** |

---

## 2. The 02:09–02:10 Fleet-Wide Power Collapse (Root Cause #1)

### Empirical Fact
Every single unit in the field operated until **02:09–02:10** and then collapsed permanently:
- `wittyBee`: Hard PMIC reset at **02:09:02** (burst 8 frame 1)
- `jumpChucla`: Hard PMIC reset at **02:09:48** (burst 8 frame 6)
- `webbedPavo`: Hard PMIC reset at **02:10:01** (burst 9 frame 3)

### Mechanism of Collapse
1. **Excessive Burst Energy Budget:**
   In `preferred_mothbox_settings.csv`, burst mode is set to 8 photos at 6.0s spacing every 1 minute = **400 photos per hour**.
   In each photo, libcamera auto-exposure and raw sensor pipeline read out the 64MP OwlSight sensor while `mothbox_hw.flash_on()` holds the high-power flash LEDs active for **1.2s to 1.5s per frame**.
   400 photos/hr × 1.4s = **560 seconds (~9.3 minutes) of continuous high-power 12V LED pulse load every active hour**.
2. **Internal Battery Resistance ($R_{int}$) Sag:**
   Across ~5 active hours, the 142Wh TalentCell packs discharged to ~11.0V–11.3V idle. At this state of charge, the dynamic current draw (Pi 5 + Attractor + Flash LEDs $\approx 1.8\text{A} - 2.5\text{A}$) induced an internal IR voltage drop of **-2.7V to -3.3V**.
3. **5V Buck Converter Dropout & Pi 5 UVLO:**
   The 12V rail plunged to **7.74V** on `webbedPavo`, **8.57V** on `wittyBee`, and **9.32V** on `jumpChucla`. When input voltage dips below ~8.5V under surge, the 5V switching regulator feeding the Pi 5 suffers voltage ripple and dropout. The Pi 5 DA9091 PMIC detects undervoltage on the 5V rail and asserts an instantaneous hardware reset/poweroff.
4. **Flawed Software Voltage Guard:**
   `TakePhoto.py` lines 384–387 had:
   ```python
   cutoff_v = 11.0 if (max_duration_s is None or max_duration_s > 2.0) else 9.8
   if 0.0 < v < cutoff_v:
       return False  # inhibit flash
   ```
   This guard checks *idle* voltage before firing and used **9.8V** as the floor. Because the battery idle voltage was 11.0V–11.3V, the software permitted the flash to fire, entirely blind to the fact that the dynamic pulse would plunge the bus by 3.3V straight into brownout!

---

## 3. The 3.3V Switched Rail & ePaper Bus Hang (Root Cause #2)

### Empirical Fact
`jumpChucla` booted successfully at 21:00:19, but captured **0 photos** and sat locked awake drawing ~0.3A until 02:00.

### Mechanism
In `Scheduler.py` lines 2788–2794:
```python
if mothbox_hw is not None:
    try:
        mothbox_hw.attract_off()
        mothbox_hw.rail_12v(False)
        mothbox_hw.rail_3v3(False)  # <-- KILLS 3.3V POWER TO SENSORS & BUS
    except Exception:
        pass
time.sleep(5.0)
run_helper("/home/pi/Desktop/Mothbox/UpdateDisplay.py", "Display error")
```
De-asserting `rail_3v3` drops power to the INA219 current/voltage monitor and DIP switch expanders. When `UpdateDisplay.py` launches and attempts to communicate over I2C and SPI, the unpowered peripheral bus locks the Pi 5 kernel SPI driver. `Scheduler.py` hangs indefinitely, the boot lock (`/run/boot_script_running`) is never cleared, and `TakePhoto.py` is permanently blocked.

---

## 4. Late RTC Alarm Arming Architecture (Root Cause #3)

### Empirical Fact
- `webbedPavo` at 00:00: crashed 3s into boot at `Time mode: AUTO (NTP)` line 2426.
- `wittyBee` at 23:00: crashed 1s into boot during hardware detection.
- `wittyBee` at 01:00: crashed 3s into boot during time sync.
- In all 3 cases, the unit completely missed the *subsequent* hour (01:00 and 00:00), sitting dead in the field.

### Architectural Flaw
`Scheduler.py` executes over 350 lines of complex hardware initialization (EEPROM checks, NTP updates, `hwclock -w`, switch reads, camera detection) **BEFORE** it computes the next schedule and writes the next wake alarm to `/sys/class/rtc/rtc0/wakealarm`.
If a single transient exception occurs in any early check, Python exits, the RTC alarm is never set, and the unit is bricked until manual intervention.

---

## 5. False Alert Night Blackout Override (Root Cause #4)

### Empirical Fact
All units that lacked an external USB drive logged `WARNING: NO USB DRIVE`.
In `Scheduler.py` line 181:
```python
if (media_warn and not is_numeric) or cam_state.lower() == "missing":
    log_warn(f"[Scheduler] Alert active -- forcing ePaper display update.")
    return True
```
This alert forced ePaper screen refreshes on every single nighttime boot (at 20:00, 21:00, 22:00, 23:00, 00:00, and 02:00), completely bypassing the `sparse` deep-night blackout rule and subjecting the sagging battery to additional power-rail stress.

---

## 6. Actionable Engineering Fixes (Preventing Regression & Toggling)

To achieve true 99%+ field reliability without circular code edits, the firmware requires 4 structural fixes:

1. **Dynamic Voltage Guard with Peak Sag Memory (`TakePhoto.py`):**
   - The flash inhibit floor must NOT be a static 9.8V idle. It must track the *sagged* voltage.
   - If the last flash sagged the rail below **10.2V** (or if current idle is below **11.2V**), inhibit flash and fall back to low-power attractor-assisted exposure, or reduce burst count from 8 to 2 frames.
   - Shorten flash pulse duration from 1.5s to 350ms (use `rpicam-still --immediate` or streaming buffer grab instead of cold full-sensor auto-exposure dwell).
2. **Early RTC Wakealarm Arming at $t=0$ (`Scheduler.py`):**
   - Move schedule computation and RTC alarm arming to the very first 10 lines of `Scheduler.py` (before hardware probes, network, or ePaper). If anything later crashes, the next hour is already permanently armed in the RTC hardware.
3. **Eliminate `rail_3v3(False)` prior to `UpdateDisplay.py`:**
   - Keep the 3.3V switched rail energized whenever the system is running displays or diagnostics.
4. **Hard Night Blackout Enforcement:**
   - Alerts must NEVER override the 22:00–05:00 blackout while unattended in `mode=ACTIVE`. Alerts should be shown on the morning wake (05:00+), not in the middle of the night.
