#!/usr/bin/python

"""
Takephoto - Andy Quitmeyer - Public Domain

This script goes through the proper setup for using 64MP cameras on a pi4 or pi5

Its order of operations is like this
-Determine if pi4 or pi5 to set max resolution
-Read in camera settings
-Configure camera settings like HDR mode
-Calibrate camera's exposure and focus (if mandated)
-prepare the camera for capturing pixels
-Turning camera flash on
-Capturing the pixels
-Turning the camera flash off as quickly as possible after
-Saving the pixels to disk


TODO:
-Add safety function to detect if disk space left is less than 7GB and refuse to take more photos, and give a debug flash pattern (such as SOS with ring lights)
"""
import os
import sys
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass
#######---- Single instance & Boot lock guards ------
import time
import fcntl
import errno

BOOT_LOCK = "/run/boot_script_running"
_photo_lock = None

def acquire_photo_lock():
    global _photo_lock
    if os.path.exists(BOOT_LOCK):
        sys.exit(0)

    for _lock_candidate in ["/run/mothbox_takephoto.lock", "/tmp/mothbox_takephoto.lock"]:
        try:
            _photo_lock = open(_lock_candidate, "a+")
            fcntl.flock(_photo_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError) as e:
            if isinstance(e, BlockingIOError) or getattr(e, "errno", None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                print("[TakePhoto] a previous photo is still in progress -- skipping this cycle")
                sys.exit(0)
            if isinstance(e, PermissionError) or getattr(e, "errno", None) == errno.EACCES:
                continue
            print(f"[TakePhoto] could not take the single-instance lock ({e}); continuing anyway")
            return True
        except Exception as e:
            print(f"[TakePhoto] could not take the single-instance lock ({e}); continuing anyway")
            return True
    return True

#-----------------------------##

import time

import os, platform
from pathlib import Path

# Control files live on the JOURNALED ext4 root now, not the FAT boot
# partition -- see mothbox_paths.py for why (Oct 2026: FAT corruption bricked
# deployed boxes). Writes go to STATE_ROOT; reads fall back to the old boot
# location so a box upgrading in place keeps its existing values.
try:
    from mothbox_paths import STATE_ROOT as _STATE_ROOT, resolve_read as _resolve_read, ensure_state_dir as _ensure_state_dir
    _ensure_state_dir()
    CONTROL_ROOT = _STATE_ROOT
except Exception:                       # module missing -> old behaviour, still runs
    CONTROL_ROOT = Path("/boot/firmware/mothbox_custom/system/controls")
    def _resolve_read(p):
        return p
LAST_PHOTO_FILE = CONTROL_ROOT / "last_photo_time.txt"


def get_photo_interval():
    path = CONTROL_ROOT / "photo_interval.txt"  # consistent with rest of file
    if not path.exists():
        return 1
    with open(path) as f:
        for line in f:
            if line.startswith("photo_interval="):
                try:
                    return int(line.split("=", 1)[1].strip())
                except ValueError:
                    return 1
    return 1


def is_photo_due():
    interval_mins = get_photo_interval()
    if interval_mins <= 1:
        return True  # Fast path — every minute, no need to check

    if not os.path.exists(LAST_PHOTO_FILE):
        return True  # First photo ever, go ahead

    try:
        with open(LAST_PHOTO_FILE) as f:
            last = float(f.read().strip())
        elapsed_mins = (time.time() - last) / 60.0
        threshold = interval_mins - 0.55  # Allow up to 33s early
        print(f"Photo interval check: {elapsed_mins:.2f} min elapsed, threshold is {threshold:.1f} min (interval={interval_mins})")
        return elapsed_mins >= threshold
    except (ValueError, IOError):
        return True  # If anything goes wrong reading the file, take the photo

def record_photo_taken():
    try:
        with open(LAST_PHOTO_FILE, "w") as f:
            f.write(str(time.time()))
            f.flush()
            os.fsync(f.fileno())
    except IOError as e:
        print(f"Warning: could not write last_photo_time: {e}")



def read_control(path: Path, key: str, default=None):
    """
    Reads a single key=value control file.
    Safe against missing, empty, or corrupted files.
    """
    # Prefer the ext4 copy; fall back to the legacy boot-partition one so a
    # box upgrading in place keeps its existing value, and so the shipped
    # read-only files (softwareversion / safetygb) are found.
    path = _resolve_read(path)
    if not path.exists():
        return default

    try:
        with open(path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == key:
                    return v.strip()
    except Exception as e:
        print(f"Warning: Failed reading {path}: {e}")

    return default

def get_capture_mode():
    mode_val = read_control(CONTROL_ROOT / "capture_mode.txt", "capture_mode", "timelapse")
    return str(mode_val).strip().lower() if mode_val else "timelapse"

def get_burst_count():
    try:
        val = int(read_control(CONTROL_ROOT / "burst_count.txt", "burst_count", 8))
        return max(1, min(60, val))
    except (ValueError, TypeError):
        return 8

def get_burst_interval_s():
    try:
        val = float(read_control(CONTROL_ROOT / "burst_interval_s.txt", "burst_interval_s", 6.0))
        return max(0.5, min(60.0, val))
    except (ValueError, TypeError):
        return 6.0



# (Mode and interval checks moved into __main__ block so importing TakePhoto as a library does not exit)




import time
from picamera2 import Picamera2, Preview
from libcamera import controls
from libcamera import Transform

import time
import datetime
from datetime import datetime, timedelta
computerName = "mothboxNOTSET"
import cv2

import csv
import sys
import shutil
import io
import queue
import threading
from PIL import Image
import piexif
try:
    import mothbox_exif  # sits next to this script; builds the full EXIF block
except Exception as _e:
    print(f"[TakePhoto] mothbox_exif unavailable ({_e}); photos will be saved without EXIF")
    mothbox_exif = None
try:
    import mothbox_hw    # which board this is (Pro PCB / DIY relay build)
    MOTHBOX_BODY = mothbox_hw.body_name()
except Exception as _e:
    print(f"[TakePhoto] mothbox_hw unavailable ({_e}); assuming Mothbox Pro")
    mothbox_hw = None
    MOTHBOX_BODY = "Mothbox Pro"
import subprocess



       
CAMERA_SETTINGS_PATH = "/boot/firmware/mothbox_custom/camera_settings.csv"
DEFAULT_CAMERA_SETTINGS_PATH = "/boot/firmware/mothbox_custom/system/controls/defaults/default_camera.txt"
AF_LENS_PATH = CONTROL_ROOT / "aflensposition.txt"
AF_GAIN_PATH=CONTROL_ROOT / "autogain.txt"
AF_EXPOSURE_PATH =CONTROL_ROOT / "exposuretime.txt"
CAPTURE_TIMEOUT_S = 20   # a healthy capture takes ~1 s; see flash watchdog notes below




internal_storage_minimum = int(
    read_control(CONTROL_ROOT / "safetygb.txt", "safetygb", 9)
)

internal_storage_minimum=internal_storage_minimum-1 # Important, this must be lower than the backup files minimum, or else the whole thing will just stall potential
extra_photo_storage_minimum=internal_storage_minimum
# Define paths
desktop_path = Path(
    "/home/pi/Desktop/Mothbox"
)  # Assuming user is "pi" on your Raspberry Pi

def restart_script():
    """
    Terminates the current script and restarts it.
    """
    print("Restarting script...")
    # A pending SIGALRM survives exec() but its handler does not: cancel the
    # photo watchdog so it cannot kill the fresh process before it re-arms.
    try:
        import signal as _sig
        _sig.alarm(0)
    except Exception:
        pass
    time.sleep(1)  # Optional: Add a small delay for clarity
    python_executable = sys.executable
    script_path = sys.argv[0]
    os.execv(python_executable, [python_executable, script_path])



def atomic_write(path: Path, content: str):
    tmp = path.with_suffix(path.suffix + ".tmp")

    with open(tmp, "w") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp, path)

def atomic_update_kv(path: Path, key: str, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    if path.exists():
        with open(path, "r") as f:
            for line in f:
                if "=" in line:
                    lines.append(line)

    found = False
    for i, line in enumerate(lines):
        if line.startswith(key + "="):
            lines[i] = f"{key}={value}\n"
            found = True

    if not found:
        lines.append(f"{key}={value}\n")

    atomic_write(path, "".join(lines))

ABORT_COUNTER_FILE = CONTROL_ROOT / "consecutive_aborted_captures.txt"
EARLY_SHUTDOWN_FILE = CONTROL_ROOT / "early_shutdown_reason.txt"

def reset_aborted_captures():
    """Reset the consecutive aborted captures counter upon successful photo save."""
    try:
        if ABORT_COUNTER_FILE.exists():
            atomic_update_kv(ABORT_COUNTER_FILE, "consecutive_aborted_captures", "0")
    except Exception:
        pass

def trigger_early_shutdown(reason="storage_full_10_aborts", mode=None):
    """
    Signals Scheduler.py to shut down early. If Scheduler.py is not running,
    invokes Scheduler.py --early-shutdown directly. Returns True if shutdown was
    dispatched, False if suppressed by mode.
    """
    current_mode = mode if mode is not None else read_control(CONTROL_ROOT / "mode.txt", "mode", "ACTIVE")
    if current_mode in ("DEBUG", "PARTY"):
        print(f"[TakePhoto] In {current_mode} mode -- suppressing early shutdown ({reason}).")
        return False

    try:
        atomic_update_kv(EARLY_SHUTDOWN_FILE, "early_shutdown_reason", reason)
    except Exception as e:
        print(f"[TakePhoto] Could not write early shutdown reason: {e}")

    try:
        if mothbox_hw is not None:
            mothbox_hw.all_lights_off()
    except Exception:
        pass

    try:
        res = subprocess.run(["pgrep", "-f", "Mothbox/Scheduler.py"], capture_output=True, text=True)
        if res.returncode == 0:
            print(f"[TakePhoto] Signaled running Scheduler.py for early shutdown ({reason}).")
        else:
            print(f"[TakePhoto] Scheduler.py not running -- invoking Scheduler.py --early-shutdown directly ({reason}).")
            subprocess.Popen(["python3", "/home/pi/Desktop/Mothbox/Scheduler.py", "--early-shutdown"])
    except Exception as e:
        print(f"[TakePhoto] Error triggering early shutdown: {e}")

    return True

def run_cmd(cmd):
    """Run a shell command safely"""
    subprocess.run(cmd, shell=True, check=False)

# The flash LEDs overheat if left on for more than about a minute, and on low
# battery packs a continuous flash (>1s) causes instantaneous voltage sag that
# trips the PMIC into an unrecoverable halt (red LED). To prevent brownouts and
# hardware damage, the hard watchdog limit is strictly bounded to 3 seconds.
FLASH_WATCHDOG_S = 3
_flash_watchdog = None

def _arm_flash_watchdog(timeout_s=None):
    global _flash_watchdog
    _disarm_flash_watchdog()
    t = timeout_s if timeout_s is not None else FLASH_WATCHDOG_S
    try:
        _flash_watchdog = subprocess.Popen(
            ["/bin/sh", "-c",
             f"sleep {t}; echo '[TakePhoto] FLASH WATCHDOG fired ({t}s) -- forcing flash off'; "
             f"python /home/pi/Desktop/Mothbox/Flash_Off.py"],
            start_new_session=True)
    except Exception as e:
        print(f"[TakePhoto] could not arm flash watchdog: {e}")

def _disarm_flash_watchdog():
    global _flash_watchdog
    if _flash_watchdog is not None:
        try:
            os.killpg(_flash_watchdog.pid, 15)     # the whole sh+sleep session
        except Exception:
            pass
        _flash_watchdog = None

# Drive the lights IN-PROCESS via mothbox_hw instead of shelling out to
# Attract_On.py / Flash_On.py / Flash_Off.py. Those were four Python
# interpreter startups per photo -- on a thermally throttled Pi that is
# seconds of pure overhead, and the flash stayed lit for all of it. The
# subprocess scripts remain for manual/cron use; this is the hot path.
def flashOff():
    if mothbox_hw is not None:
        mothbox_hw.flash_off()
    else:
        run_cmd("python /home/pi/Desktop/Mothbox/Flash_Off.py")
    _disarm_flash_watchdog()
    print("Flash Off\n")

def flashOn(max_duration_s=None):
    # Pre-flash voltage gate: query INA219/INA260 to inhibit flash if battery is critically low.
    # Short burst pulses (<=1.0s) are safe down to 9.8V under attractor load (well above the 8.8V
    # PMIC reset floor); longer continuous pulses use a stricter 11.0V cutoff.
    if mothbox_hw is not None:
        try:
            pwr = mothbox_hw.read_power()
            v = pwr.get("voltage_v", -1.0)
            cutoff_v = 11.0 if (max_duration_s is None or max_duration_s > 2.0) else 9.8
            if 0.0 < v < cutoff_v:
                print(f"[TakePhoto] WARNING: Battery voltage ({v:.2f}V < {cutoff_v:.1f}V cutoff) -- INHIBITING FLASH to prevent PMIC halt/brownout!")
                return False
        except Exception as e:
            print(f"[TakePhoto] Voltage check error ({e}) -- proceeding with caution.")

    if mothbox_hw is not None:
        mothbox_hw.flash_on()
    else:
        run_cmd("python /home/pi/Desktop/Mothbox/Flash_On.py")
    _arm_flash_watchdog(timeout_s=max_duration_s)
    print("Flash On\n")
    return True

def is_csv_valid(filepath):
    if not os.path.exists(filepath):
        return False
    if os.path.getsize(filepath) < 10:
        return False

    try:
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            required = {"SETTING", "VALUE", "DETAILS"}
            return required.issubset(reader.fieldnames or [])
    except Exception:
        return False


def restore_default_camera_csv():
    print("Camera settings corrupted — restoring defaults")
    if not os.path.exists(DEFAULT_CAMERA_SETTINGS_PATH):
        print("ERROR: Default camera settings file also missing:", DEFAULT_CAMERA_SETTINGS_PATH)
        return False
    os.makedirs(os.path.dirname(CAMERA_SETTINGS_PATH), exist_ok=True)
    shutil.copy2(DEFAULT_CAMERA_SETTINGS_PATH, CAMERA_SETTINGS_PATH)
    return True

def auto_cast_value(setting, value):
    """
    Convert known camera settings to proper types.
    Everything else: best-effort numeric cast, else string.
    """

    # Explicit rules
    if setting in ("AeEnable", "AwbEnable"):
        return value.lower() in ("1", "true", "yes", "on")

    if setting in ("LensPosition", "AnalogueGain", "ExposureValue",
                   "LensPositionMin", "LensPositionMax"):
        return float(value)

    if setting == "VerticalFlip" and str(value).strip().lower() == "auto":
        return "auto"          # resolved per hardware below (Pro 0, DIY 1)

    if setting in (
        "ExposureTime", "AwbMode", "AfTrigger",
        "AfRange", "AfSpeed", "AfMode",
        "HDR", "HDR_width",
        "AutoCalibration", "AutoCalibrationPeriod",
        "ImageFileType", "VerticalFlip",
        "onlyflash"
    ):
        return int(float(value))

    # Best-effort fallback casting:
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


# Plausible autofocus range, in diopters (1/metres), from camera_settings.csv.
# Default is deliberately broad -- 6 cm (16.7) to 2.5 m (0.4) -- so every
# normal Mothbox setup passes; tighten it per box for the guard to bite
# harder. This is a SEPARATE safeguard from the AF-failed check below:
# that one catches a covered lens (scan fails); this one catches a scan that
# SUCCEEDS on the wrong thing -- e.g. something small right in front of the
# lens -- by rejecting a focus outside the range this box is known to use.
AF_LENS_MIN = 0.4    # 2.5 m
AF_LENS_MAX = 16.7   # 6 cm

def extract_af_limits(settings):
    """Pop LensPositionMin/Max out of the settings dict (picamera2 would reject them as controls)."""
    global AF_LENS_MIN, AF_LENS_MAX
    AF_LENS_MIN = float(settings.pop("LensPositionMin", AF_LENS_MIN))
    AF_LENS_MAX = float(settings.pop("LensPositionMax", AF_LENS_MAX))
    if AF_LENS_MIN > AF_LENS_MAX:
        print(f"[TakePhoto] LensPositionMin {AF_LENS_MIN} > LensPositionMax {AF_LENS_MAX} -- ignoring both")
        AF_LENS_MIN, AF_LENS_MAX = 0.4, 16.7


def load_camera_settings(retrying=False):
    global middleexposure, calib_lens_position, calib_exposure

    if not is_csv_valid(CAMERA_SETTINGS_PATH):
        restore_default_camera_csv()

    try:
        with open(CAMERA_SETTINGS_PATH, newline="") as csv_file:
            reader = csv.DictReader(csv_file)
            the_camera_settings = {}

            for row in reader:
                setting = row.get("SETTING", "").strip()
                value   = row.get("VALUE", "").strip()

                if not setting:
                    continue

                value = auto_cast_value(setting, value)

                if setting == "ExposureTime":
                    middleexposure = value
                    print("middleexposurevalue", middleexposure)

                the_camera_settings[setting] = value

            return the_camera_settings

    except Exception as e:
        print(f"Camera settings load failure: {e}")
        if retrying:
            print("Default camera settings also failed, using empty settings")
            return {}
        print("Reverting to default camera settings")
        restore_default_camera_csv()
        return load_camera_settings(retrying=True)


def atomic_write_csv(path, rows, fieldnames):
    """
    Atomically writes CSV file contents.
    Power-loss safe.
    """
    tmp_path = path + ".tmp"

    with open(tmp_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
        f.flush()
        os.fsync(f.fileno())

    os.replace(tmp_path, path)


def update_camera_settings(filename, new_settings):
    """
    Safely updates the values in a CSV file using atomic replacement.
    Power-loss safe.

    Args:
        filename (str): Path to CSV file.
        new_settings (dict): Dictionary of key → new value
    """

    # Ensure file exists — if missing, restore defaults first
    if not is_csv_valid(filename):
        restore_default_camera_csv()

    try:
        with open(filename, newline="") as csvfile:
            reader = csv.DictReader(csvfile)
            fieldnames = reader.fieldnames
            rows = []

            for row in reader:
                setting = row.get("SETTING", "").strip()
                if setting in new_settings:
                    row["VALUE"] = str(new_settings[setting])
                rows.append(row)

        atomic_write_csv(filename, rows, fieldnames)

    except Exception as e:
        print(f" Failed updating camera CSV safely: {e}")
        
        
def get_serial_number():
  """
  This function retrieves the Raspberry Pi's serial number from the CPU info file.
  """
  try:
    with open('/proc/cpuinfo', 'r') as cpuinfo:
      for line in cpuinfo:
        if line.startswith('Serial'):
          return line.split(':')[1].strip()
  except (IOError, IndexError):
    return None

def stop_cron():
    """Runs the command 'service cron stop' to stop the cron service."""
    try:
        subprocess.run(["sudo", "service", "cron", "stop"], check=True)
        print("Cron service stopped successfully.")
    except subprocess.CalledProcessError as error:
        print("Error stopping cron service:", error)

def start_cron():
    """Runs the command 'service cron stop' to stop the cron service."""
    try:
        subprocess.run(["sudo", "service", "cron", "start"], check=True)
        print("Cron service started successfully.")
    except subprocess.CalledProcessError as error:
        print("Error starting cron service:", error)
        
def print_af_state(request):
    md = request.get_metadata()
    #print(("Idle", "Scanning", "Success", "Fail")[md['AfState']], md.get('LensPosition'))
def run_calibration():
    global calib_lens_position, calib_exposure, camera_settings, width, height, picam2
    #preview_config = picam2.create_preview_configuration(main={'format': 'RGB888', 'size': (4624, 3472)})
    preview_config = picam2.create_preview_configuration(main={'size': (1920*2, 1080*2)})
    #still_config = picam2.create_still_configuration(main={"size": (width, height), "format": "RGB888"}, buffer_count=1)
    picam2.configure(preview_config)

    
    #picam2.set_controls({"AfMode":0,"AfSpeed":0,"AfRange":0, "LensPosition":7.0})

    
    #time.sleep(1)
    picam2.pre_callback = print_af_state
    
    
    time.sleep(2)
    picam2.set_controls({"LensPosition":7.0})
    #picam2.set_controls({"AfSpeed":controls.AfSpeedEnum.Fast})

    
    exposurevalue=camera_settings["ExposureValue"]
    picam2.set_controls({"ExposureValue":exposurevalue})# Floating point number between -8.0 and 8.0
    picam2.set_controls({"ExposureTime":500}) #we want a fast photo so we don't get blurry insects. We lock the exposure time and adjust gain. Values below the sensor's floor are clamped by libcamera (OwlSight full-res: 1119us at link-frequency 360MHz, 868us at 456MHz); the real value lands in the EXIF


    time.sleep(1)

    print("!!! Autofocusing !!!")
    afstart = time.time()
    use_flash_for_af = True
    if mothbox_hw is not None:
        try:
            pwr = mothbox_hw.read_power()
            v = pwr.get("voltage_v", -1.0)
            if 0.0 < v < 11.8:
                print(f"[TakePhoto] Battery voltage ({v:.2f}V < 11.8V) -- using single channel (CH1) for AF to prevent regulator overload.")
                use_flash_for_af = False
                if mothbox_hw.is_pro():
                    mothbox_hw.rail_12v(True)
                    mothbox_hw._out(mothbox_hw.PRO["CH1"], True)
                else:
                    mothbox_hw._out(mothbox_hw.DIY["RELAY_ATTRACT_A"], False)
        except Exception:
            pass
    if use_flash_for_af:
        flashOn(max_duration_s=4.0)
    start_camera_or_warn(show_preview=False)
    #picam2.start()
    
    for i in range(5):
        if i == 15:
            pass
            #picam2.set_controls({'AnalogueGain': 4.0})
            #picam2.set_controls({"ExposureValue":-4.0})# Floating point number between -8.0 and 8.0

        elif i == 50:
            pass
            #picam2.set_controls({'AnalogueGain': 1.2})
            #picam2.set_controls({"ExposureValue":8.0})# Floating point number between -8.0 and 8.0

        md = picam2.capture_metadata()
        print(i, "Calibrating for BRIGHTNESS--  exposure: ", md['ExposureTime'],"  gain: ", md['AnalogueGain'], "  Lensposition:", md['LensPosition'])
    
    md = picam2.capture_metadata()
    calib_exposure = md['ExposureTime']
    autogain= md['AnalogueGain']

    print("Exposure: "+str(calib_exposure))
    print("Autogain: "+str(autogain))
    
    time.sleep(.1) #give a tiny bit of time to let the flash start up

    #picam2.set_controls({"AfMode": 2})
    #time.sleep(7)
    print("Running autofocus...")
    #picam2.start(show_preview=True, ) #preview has to be on for some reason to work
    success = picam2.autofocus_cycle()

    flashOff()
    if not use_flash_for_af and mothbox_hw is not None:
        if mothbox_hw.is_pro():
            mothbox_hw._out(mothbox_hw.PRO["CH1"], False)
            mothbox_hw.rail_12v(False)
        else:
            mothbox_hw._out(mothbox_hw.DIY["RELAY_ATTRACT_A"], True)
    print("Autofocus completed! "+str(time.time()-afstart))
    md = picam2.capture_metadata()
    
    

    new_lens_position = md['LensPosition']
    focusstate = md['AfState']           # libcamera: 0 Idle, 1 Scanning, 2 Focused, 3 Failed
    AF_STATE_NAMES = {0: "Idle", 1: "Scanning", 2: "Focused", 3: "Failed"}

    # Safeguard 1: only trust the scan if libcamera says it focused. Anything
    # covering the lens -- the cap, or a moth sitting on it -- makes the scan
    # fail and leaves the lens wherever it stopped, often 0.0 (infinity).
    # Saving that used to make every photo blurry until the next calibration.
    # Safeguard 2 (optional, LensPositionMin/Max in camera_settings.csv): a scan
    # that succeeded but outside the range this box is known to use. Defaults
    # are wide open, so with no settings any successful focus is accepted,
    # whether the subject is 10 cm or 2 m away.
    if not (success and focusstate == 2):
        print(f"[TakePhoto] AUTOFOCUS FAILED (AfState={AF_STATE_NAMES.get(focusstate, focusstate)}, "
              f"lens ended at {new_lens_position}) -- something is probably covering the lens; "
              f"keeping the previous lens position {calib_lens_position}")
    elif not (AF_LENS_MIN <= new_lens_position <= AF_LENS_MAX):
        where = f"~{100.0/new_lens_position:.0f} cm" if new_lens_position > 0 else "infinity"
        print(f"[TakePhoto] AUTOFOCUS REJECTED: focused at {new_lens_position} diopters ({where}) "
              f"but this box allows {AF_LENS_MIN}-{AF_LENS_MAX} (LensPositionMin/Max in "
              f"camera_settings.csv) -- keeping the previous lens position {calib_lens_position}")
    else:
        calib_lens_position = new_lens_position
        atomic_update_kv(AF_LENS_PATH, "aflensposition", calib_lens_position)
        print(f"LensPosition: {calib_lens_position}  (AfState {AF_STATE_NAMES.get(focusstate, focusstate)})")


    #camera_settings["LensPosition"]=calib_lens_position
    
    #camera_settings["ExposureTime"]=calib_exposure
    atomic_update_kv(AF_EXPOSURE_PATH, "exposuretime", calib_exposure)

    #camera_settings["AnalogueGain"]=autogain
    atomic_update_kv(AF_GAIN_PATH, "autogain", autogain)

    picam2.stop()
    picam2.stop_preview()
    
    #save last time
    #set_last_calibration(control_values_fpath)
    atomic_update_kv(CONTROL_ROOT / "lastcalibration.txt", "lastcalibration", str(time.time()))

    #save the calibrated settings back to the CSV
    #new_settings = {"LensPosition": calib_lens_position, "ExposureTime": calib_exposure, "AnalogueGain": autogain} 
    #update_camera_settings(chosen_settings_path, new_settings)
    
    #restart the whole script now because for some reason if we just run the phot taking it is always slightly brighter
    time.sleep(1)
    restart_script()
    

def list_exposuretimes(middle_exposuretime, num_photos, exposure_width):
  """
  This function calculates exposure times for HDR photos.

  Args:
      middle_exposuretime: The middle exposure time in microseconds.
      num_photos: The number of photos to take.
      exposure_width: The exposure width in steps (added/subtracted to middle time).

  Returns:
      A list of exposure times in microseconds for each HDR photo.
  """
  
  exposure_times = []
  half_num_photos =  int((num_photos -1) / 2)  # Ensure at least one photo on each side
  #print(half_num_photos)
  # Start with middle exposure for the first photo
  current_exposure = middle_exposuretime
  exposure_times.append(current_exposure)

  # Loop for positive adjustments (excluding middle)
  for i in range(1, half_num_photos+1):
    direction = 1
    current_exposure = middle_exposuretime+ direction * exposure_width * i
    exposure_times.append(current_exposure)

  # Loop for negative adjustments (excluding middle, if applicable)
  for i in range(half_num_photos):
    direction = -1
    current_exposure = middle_exposuretime+direction * exposure_width * (i + 1)  # Adjust index for missing middle photo
    exposure_times.append(current_exposure)
  return exposure_times

def create_dated_folder(base_path):
  """
  Creates a folder with the current date in the format YYYY-MM-DD if it doesn't exist.

  Args:
      base_path: The base path where the folder will be created.

  Returns:
      The full path to the created folder.
  """
  now = datetime.now()
  # Adjust for time between 12:00 pm and 11:59 am next day
  if 12 <= now.hour < 24:
    date_str = now.strftime("%Y-%m-%d")
  else:
    # Add a day if time is between 12:00 pm and next day's 11:59 am
    date_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")
  folder_path = os.path.join(base_path, computerName+"_"+date_str)
  if not os.path.exists(folder_path):
    os.makedirs(folder_path)
  os.chmod(folder_path, 0o777)  # mode=0o777 for read write for all users
  return folder_path+"/"

def takePhoto_Manual():
    global middleexposure, calib_lens_position, calib_exposure, calib_gain
    # LensPosition: Manual focus, Set the lens position.

    # Create ISO 8601 like standard timestamp on photos
    now = datetime.now()
    tz_offset = datetime.utcnow()
    offset_total_minutes = int(utc_offset_hours * 60)
    sign = "+" if offset_total_minutes >= 0 else "-"
    abs_minutes = abs(offset_total_minutes)
    offset_str = f"{sign}{abs_minutes // 60:02d}-{abs_minutes % 60:02d}"
    timestamp = now.strftime("%Y-%m-%dT%H-%M-%S") + offset_str

    '''
    this should make timestamps like

    verdeMoth_2025-06-09T21-30-00+02-00.jpg
    verdetMoth_2025-06-09T21-30-00-05-00_HDR1.jpg
    
    '''

    ''''''
    if camera_settings:
        picam2.set_controls(camera_settings)
    else:
        print("can't set controls")
    ''''''
    min_exp, max_exp, default_exp = picam2.camera_controls["ExposureTime"]
    #print(min_exp,"   ", max_exp,"   ", default_exp)


    #important note, to actually 100% lock down an AWB you need to set ColourGains! (0,0) works well for plain white LEDS
    cgains = 2.25943877696990967, 1.500129925489425659
    picam2.set_controls({"ColourGains": cgains})
   
    middleexposure = camera_settings["ExposureTime"]
    #middleexposure = calib_exposure  # this is more correct i think, but it's messing it up if it is here!
    exposure_times = list_exposuretimes(middleexposure, num_photos,exposuretime_width)
    print(exposure_times)
    
    time.sleep(1)
    start_camera_or_warn()
        
    time.sleep(3)

    start = time.time()

    if(num_photos>2):
        print("About to take HDR photo:  ",timestamp)
    else:
        print("About to take single photo:  ",timestamp)



    exposureset_delay=.3 #values less than 5 don't seem to work! (unless you restart the cam!)
    requests = []  # Create an empty list to store requests
    PILs = []
    metadatas = []
    flash_statuses = []
    #HDR loop
    for i in range(num_photos):
        #middleexposure = camera_settings["ExposureTime"]
        
        picam2.set_controls({"ExposureTime":exposure_times[i] })
        print("exp  ",exposure_times[i],"  ",i)
        #picam2.set_controls({"NoiseReductionMode":controls.draft.NoiseReductionModeEnum.HighQuality})
        start_camera_or_warn() #need to restart camera or wait a couple frames for settings to change

        time.sleep(exposureset_delay)#need some time for the settings to sink into the camera)
        
        t_flash_start = time.monotonic()
        p_idle = mothbox_hw.read_power() if mothbox_hw else {"voltage_v": -1.0, "current_a": 0.0}
        flash_fired = flashOn(max_duration_s=1.5)
        request = None
        p_flash = None
        try:
            if flash_fired:
                time.sleep(0.005)  # 5ms MOSFET gate & phosphor stabilization buffer
            try:
                request = picam2.capture_request(flush=True, wait=CAPTURE_TIMEOUT_S)
                if flash_fired and mothbox_hw:
                    p_flash = mothbox_hw.read_power()
            except TypeError:
                request = picam2.capture_request(flush=True)
                if flash_fired and mothbox_hw:
                    p_flash = mothbox_hw.read_power()
        except Exception as e:
            flashOff()
            warn_no_camera(f"no frame from camera: {e.__class__.__name__}: {e}")
        finally:
            flashOff()
            t_flash_dur = time.monotonic() - t_flash_start
            if flash_fired:
                if p_flash and p_flash.get("voltage_v", -1) > 0 and p_idle.get("voltage_v", -1) > 0:
                    v_idle = p_idle["voltage_v"]
                    v_fl = p_flash["voltage_v"]
                    a_fl = p_flash["current_a"]
                    sag = v_idle - v_fl
                    print(f"[TakePhoto] Photo {i + 1} flash: {t_flash_dur * 1000.0:.1f}ms | Idle: {v_idle:.2f}V -> Flash: {v_fl:.2f}V @ {a_fl:.3f}A (sag -{sag:.2f}V)", flush=True)
                else:
                    print(f"[TakePhoto] Photo {i + 1} flash strobe duration: {t_flash_dur * 1000.0:.1f}ms (single frame covered)", flush=True)

        flashtime=time.time()-start

        pilImage = request.make_image("main")
        PILs.append(pilImage)
        metadatas.append(request.get_metadata())
        flash_statuses.append(flash_fired)
        request.release()

        picam2.stop()
        print("picture take time: "+str(flashtime))
        
    # Gathered once per run for the EXIF: which sensor libcamera says this is,
    # and the most recent diagnostics readings already logged. Nothing is
    # measured here -- that would cost seconds per photo.
    try:
        camera_sensor_name = picam2.camera_properties.get("Model", "")
    except Exception:
        camera_sensor_name = ""
    latest_diagnostics = mothbox_exif.read_latest_diagnostics() if mothbox_exif else {}

    # Saving loop (can be done later)
    i=0
    for img in PILs:  
          exif_data=metadatas[i]
          pil_image = img
          # Save the image using PIL to get the image data on disk
          folderPath= "/home/pi/Desktop/Mothbox/photos/" #can't use relative directories with cron
          if not os.path.exists(folderPath):
            os.makedirs(folderPath)
          os.chmod(folderPath, 0o777)  # mode=0o777 for read write for all users

          folderPath = create_dated_folder(folderPath)
          
          
          print(ImageFileType)
          
          # Only append the HDR tag if we are taking more than 1 bracketed photo
          hdr_suffix = f"_HDR{i}" if num_photos > 1 else ""
          
          if ImageFileType==1: #png
              filepath = folderPath + computerName + "_" + timestamp + hdr_suffix + ".png"
          elif ImageFileType==0: #jpeg
              filepath = folderPath + computerName + "_" + timestamp + hdr_suffix + ".jpg"
          elif ImageFileType==2: #bmp
              filepath = folderPath + computerName + "_" + timestamp + hdr_suffix + ".bmp"

        
          # --- EXIF: built by mothbox_exif.py from the REAL capture metadata ---
          # (actual exposure / gain / lens position, camera module facts, GPS,
          # and the latest diagnostics readings already on disk). Any failure
          # in there just saves the photo without EXIF -- never loses it.
          save_kwargs = {"quality": 96}
          if mothbox_exif is not None:
              photo_flash_fired = flash_statuses[i] if i < len(flash_statuses) else False
              exif_bytes = mothbox_exif.build_exif_bytes(
                  metadata=exif_data,
                  requested_exposure_us=exposure_times[i],
                  image_size=img.size,
                  capture_time=now,
                  utc_offset_hours=utc_offset_hours,
                  computer_name=computerName,
                  software_version=softwareversion,
                  sensor_name=camera_sensor_name,
                  lat=gps_lat, lon=gps_lon,
                  diagnostics=latest_diagnostics,
                  hdr_index=i, hdr_count=num_photos,
                  image_id=computerName + "_" + timestamp + hdr_suffix,
                  body_model=MOTHBOX_BODY,
                  flash_fired=photo_flash_fired,
              )
              if exif_bytes:
                  save_kwargs["exif"] = exif_bytes
          img.save(filepath, **save_kwargs)
          print("Image saved to "+filepath)
          reset_aborted_captures()
          i=i+1


class PhotoSaveWorker:
    """
    Background worker that encodes and writes images to disk with EXIF.
    Decouples sensor exposure pacing from disk I/O latency to preserve
    timing stability and keep RAM footprint minimal.
    """
    def __init__(self, computer_name, utc_offset, sw_version, body_model, gps_lat, gps_lon, max_queue_size=10):
        self.q = queue.Queue(maxsize=max_queue_size)
        self.computer_name = computer_name
        self.utc_offset = utc_offset
        self.sw_version = sw_version
        self.body_model = body_model
        self.gps_lat = gps_lat
        self.gps_lon = gps_lon
        self.saved_count = 0
        self.error_count = 0
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def enqueue(self, item):
        self.q.put(item)

    def _run(self):
        latest_diagnostics = mothbox_exif.read_latest_diagnostics() if mothbox_exif else {}
        while True:
            item = self.q.get()
            if item is None:
                self.q.task_done()
                break
            try:
                if len(item) >= 10:
                    (pil_image, metadata, shot_dt, exp_us, idx, total_count, suffix, sensor_name, file_type, flash_fired) = item[:10]
                else:
                    (pil_image, metadata, shot_dt, exp_us, idx, total_count, suffix, sensor_name, file_type) = item
                    flash_fired = False
                offset_total_minutes = int(self.utc_offset * 60)
                sign = "+" if offset_total_minutes >= 0 else "-"
                abs_minutes = abs(offset_total_minutes)
                offset_str = f"{sign}{abs_minutes // 60:02d}-{abs_minutes % 60:02d}"
                timestamp = shot_dt.strftime("%Y-%m-%dT%H-%M-%S") + offset_str

                folderPath = "/home/pi/Desktop/Mothbox/photos/"
                if not os.path.exists(folderPath):
                    os.makedirs(folderPath, exist_ok=True)
                os.chmod(folderPath, 0o777)
                folderPath = create_dated_folder(folderPath)

                ext = ".jpg"
                if file_type == 1:
                    ext = ".png"
                elif file_type == 2:
                    ext = ".bmp"

                filepath = os.path.join(folderPath, f"{self.computer_name}_{timestamp}{suffix}{ext}")

                save_kwargs = {"quality": 96}
                if mothbox_exif is not None:
                    exif_bytes = mothbox_exif.build_exif_bytes(
                        metadata=metadata,
                        requested_exposure_us=exp_us,
                        image_size=pil_image.size,
                        capture_time=shot_dt,
                        utc_offset_hours=self.utc_offset,
                        computer_name=self.computer_name,
                        software_version=self.sw_version,
                        sensor_name=sensor_name,
                        lat=self.gps_lat, lon=self.gps_lon,
                        diagnostics=latest_diagnostics,
                        hdr_index=idx, hdr_count=total_count,
                        image_id=f"{self.computer_name}_{timestamp}{suffix}",
                        body_model=self.body_model,
                        flash_fired=flash_fired,
                    )
                    if exif_bytes:
                        save_kwargs["exif"] = exif_bytes

                pil_image.save(filepath, **save_kwargs)
                print(f"[TakePhoto] Saved burst image (flash={'ON' if flash_fired else 'OFF'}): {filepath}")
                self.saved_count += 1
                reset_aborted_captures()
            except Exception as e:
                print(f"[TakePhoto] Error saving burst image ({e})")
                self.error_count += 1
            finally:
                self.q.task_done()

    def wait_done(self, timeout=45):
        self.q.put(None)
        self.thread.join(timeout=timeout)


def takePhoto_Burst(burst_count=8, interval_s=6.0):
    global middleexposure, calib_lens_position, calib_exposure, calib_gain
    print(f"\n================= STARTING BURST CAPTURE ({burst_count}X @ {interval_s:.1f}s) =================")

    # 1. Apply camera controls ONCE (locked exposure, gain, focus, white balance)
    if camera_settings:
        picam2.set_controls(camera_settings)

    cgains = (2.25943877696990967, 1.500129925489425659)
    picam2.set_controls({"ColourGains": cgains})

    exp_time = camera_settings.get("ExposureTime", 500)
    picam2.set_controls({"ExposureTime": exp_time})

    try:
        camera_sensor_name = picam2.camera_properties.get("Model", "")
    except Exception:
        camera_sensor_name = ""

    # 2. Start the camera stream ONCE
    start_camera_or_warn()
    time.sleep(1.0)  # Settle time for stream and VCM autofocus coil

    save_worker = PhotoSaveWorker(
        computer_name=computerName,
        utc_offset=utc_offset_hours,
        sw_version=softwareversion,
        body_model=MOTHBOX_BODY,
        gps_lat=gps_lat,
        gps_lon=gps_lon,
        max_queue_size=max(10, burst_count + 2),
    )

    t0 = time.monotonic()
    successful_captures = 0

    for k in range(burst_count):
        # Monotonic deadline pacing prevents drift
        target_deadline = t0 + (k * interval_s)
        now_mono = time.monotonic()
        sleep_dur = target_deadline - now_mono
        if sleep_dur > 0:
            time.sleep(sleep_dur)

        shot_time = datetime.now()
        elapsed_shot = time.monotonic() - t0
        print(f"[Burst] Capturing frame {k + 1}/{burst_count} (target={k * interval_s:.1f}s, actual={elapsed_shot:.2f}s)...")

        # Pulse flash for this single frame with rolling shutter window coverage
        t_flash_start = time.monotonic()
        p_idle = mothbox_hw.read_power() if mothbox_hw else {"voltage_v": -1.0, "current_a": 0.0}
        flash_fired = flashOn(max_duration_s=1.5)
        request = None
        p_flash = None
        try:
            if flash_fired:
                time.sleep(0.005)  # 5ms MOSFET gate & phosphor stabilization buffer
            try:
                request = picam2.capture_request(flush=True, wait=CAPTURE_TIMEOUT_S)
                if flash_fired and mothbox_hw:
                    p_flash = mothbox_hw.read_power()
            except TypeError:
                request = picam2.capture_request(flush=True)
                if flash_fired and mothbox_hw:
                    p_flash = mothbox_hw.read_power()
        except Exception as e:
            flashOff()
            warn_no_camera(f"no frame from camera in burst frame {k + 1}: {e.__class__.__name__}: {e}")
            break
        finally:
            flashOff()
            t_flash_dur = time.monotonic() - t_flash_start
            if flash_fired:
                if p_flash and p_flash.get("voltage_v", -1) > 0 and p_idle.get("voltage_v", -1) > 0:
                    v_idle = p_idle["voltage_v"]
                    v_fl = p_flash["voltage_v"]
                    a_fl = p_flash["current_a"]
                    sag = v_idle - v_fl
                    print(f"[Burst] Frame {k + 1} flash: {t_flash_dur * 1000.0:.1f}ms | Idle: {v_idle:.2f}V -> Flash: {v_fl:.2f}V @ {a_fl:.3f}A (sag -{sag:.2f}V)", flush=True)
                else:
                    print(f"[Burst] Frame {k + 1} flash strobe duration: {t_flash_dur * 1000.0:.1f}ms (single frame covered)", flush=True)

        try:
            pil_image = request.make_image("main")
            md = request.get_metadata()
            request.release()
            successful_captures += 1
            suffix = f"_b{k + 1}"
            save_worker.enqueue((
                pil_image, md, shot_time, exp_time,
                k, burst_count, suffix, camera_sensor_name, ImageFileType, flash_fired
            ))
        except Exception as e:
            print(f"[TakePhoto] Failed to process burst frame {k + 1}: {e}")

    # 3. Stop camera stream ONCE
    print(f"\n[Burst] Capture sequence finished ({successful_captures}/{burst_count} frames). Stopping camera stream.")
    picam2.stop()

    # 4. Drain asynchronous save queue
    print("[Burst] Waiting for image save worker to finish writing...")
    save_worker.wait_done(timeout=45)
    print(f"[Burst] Complete: {save_worker.saved_count} photos successfully written to disk.")
    return successful_captures > 0


def determinePiModel():

  # Check Raspberry Pi model using CPU info
  cpuinfo = open("/proc/cpuinfo", "r")
  model = None  # Initialize model variable outside the loop
  themodel=None

  for line in cpuinfo:
    #print(line)
    if line.startswith("Model"):
      model = line.split(":")[1].strip()
      break
  cpuinfo.close()

  # Execute function based on model
  print(model)
  if model:  # Check if model was found
    if "Pi 4" in model:  # Model identifier for Raspberry Pi 4
      themodel=4
    elif "Pi 5" in model:  # Model identifier for Raspberry Pi 5
      themodel=5
    else:
      print("Unknown Raspberry Pi model detected. Going to treat as model 5")
      themodel=5
  else:
    print("Error: Could not read Raspberry Pi model information.")
    themodel=5
  return themodel

def get_storage_info(path):
    """
    Gets the total and available storage space of a path.
    Args:
        path: The path to the storage device.

    Returns:
        A tuple containing the total and available storage in bytes.
    """
    try:
        stat = os.statvfs(path)
        return stat.f_blocks * stat.f_bsize, stat.f_bavail * stat.f_bsize
    except OSError:
        return 0, 0  # Handle non-existent or inaccessible storages

#---------------MAIN CODE--------------------- #

if __name__ == "__main__":
    acquire_photo_lock()
    mode = read_control(CONTROL_ROOT / "mode.txt", "mode", "ACTIVE")
    if mode in ("OFF", "STANDBY"):
        print("Mothbox is in", mode, "mode, not taking photos")
        sys.exit(0)

    if not is_photo_due():
        print(f"Photo not due yet (interval={get_photo_interval()} min). Skipping.")
        sys.exit(0)

    print("----------------- STARTING TAKEPHOTO-------------------")
    now = datetime.now()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")  # Adjust the format as needed
    
    print(f"Current time: {formatted_time}")
    
    
    # 1. Critical battery voltage pre-check (<9.8V PMIC brownout safety floor)
    # Prevents taking captures or firing flashes when battery voltage is critically depleted.
    try:
        if mothbox_hw is not None:
            pwr = mothbox_hw.read_power()
            v = float(pwr.get("voltage_v", -1))
            if 0.0 < v < 9.8:
                time.sleep(1.0)
                pwr = mothbox_hw.read_power()
                v = float(pwr.get("voltage_v", -1))
                if 0.0 < v < 9.8:
                    print(f"[CRITICAL] Battery resting voltage {v:.2f}V is below 9.8V brownout floor! Aborting capture to prevent brownout.")
                    trigger_early_shutdown(reason=f"critical_battery_{v:.2f}V")
                    quit()
    except Exception as e:
        print(f"[TakePhoto] Battery pre-check exception: {e}")
    
    # 2. Storage availability check and 10-consecutive-abort guard
    # First check and see if we have enough storage left to keep taking photos, or else do nothing
    # Get total and available space on desktop and external storage
    desktop_total, desktop_available = get_storage_info(desktop_path)
    print("Desktop Total    Storage: \t" + str(desktop_total))
    print("Desktop Available Storage: \t" + str(desktop_available))
    x = extra_photo_storage_minimum
    print("Minimum storage needed: \t" + str(x * 1024**3))
    
    if desktop_available < x * 1024**3:  # x GB in bytes
        current_mode = read_control(CONTROL_ROOT / "mode.txt", "mode", "ACTIVE")
        if current_mode in ("DEBUG", "PARTY"):
            print(f"[TakePhoto] Low storage in {current_mode} mode ({desktop_available / (1024**3):.2f} GB available < {x} GB minimum). Skipping capture, early shutdown suppressed.")
            quit()
        aborted_count = int(read_control(ABORT_COUNTER_FILE, "consecutive_aborted_captures", 0)) + 1
        atomic_update_kv(ABORT_COUNTER_FILE, "consecutive_aborted_captures", str(aborted_count))
        print(f"[STORAGE ALERT] Not enough space to take more photos ({desktop_available / (1024**3):.2f} GB available < {x} GB minimum). Consecutive aborted captures: {aborted_count}/10")
        if aborted_count >= 10:
            print(f"[CRITICAL] Storage full: 10 consecutive capture attempts aborted. Initiating graceful early shutdown.")
            trigger_early_shutdown(reason="storage_full_10_aborts")
        quit()
    
    
    
    
    #First figure out if this is a Pi4 or a Pi5
    rpiModel=None
    rpiModel=determinePiModel()
    
    #default resolution
    width=9000
    height=6000
    
    #the Pi4 can't really handle the FULL resolution, but pi5 can!
    if(rpiModel==5):
        width=9248
        height=6944
    
    
    #I don't really know why we need this below code, but it's here. it may have been an earlier attempt to find the pi model
    if platform.system() == "Windows":
    	print(platform.uname().node)
    else:
    	#computerName = os.uname()[1]
    	print(os.uname()[1])   # doesnt work on windows
    
    
    #HDR Controls
    CAPTURE_TIMEOUT_S = 20   # a healthy capture takes ~1 s; see flash watchdog notes above
    num_photos = 1
    exposuretime_width = 18000
    middleexposure=500 # requested exposure (us). Below the OwlSight's floor, so it is clamped to the minimum the sensor mode allows
    
    
    #global onlyflash
    #onlyflash=False
    
    
    
    #control_values_fpath = "/boot/firmware/mothbox_custom/system/controls.txt"
    #control_values = get_control_values(control_values_fpath)
    
    #onlyflash = control_values.get("OnlyFlash", "True").lower() == "true"
    #onlyflash = read_control(CONTROL_ROOT / "onlyflash.txt", "onlyflash", "0")
    
    #LastCalibration = float(control_values.get("LastCalibration", 0))
    LastCalibration= float(read_control(CONTROL_ROOT / "lastcalibration.txt", "lastcalibration", 0))
    
    
    #computerName = control_values.get("name", "wrong")
    computerName = read_control(CONTROL_ROOT / "name.txt", "name", "errorname")
    softwareversion = read_control(CONTROL_ROOT / "softwareversion.txt", "softwareversion", "5")
    # GPS fix written by GPS.py at boot; "n/a" (or missing) simply means no GPS tags.
    gps_lat = read_control(CONTROL_ROOT / "lat.txt", "lat", None)
    gps_lon = read_control(CONTROL_ROOT / "lon.txt", "lon", None)
    
    # Read UTC offset (stored as a float like 2.0 or -5.5 cuz fun timezones like kathmandu)
    utc_offset_hours = float(read_control(CONTROL_ROOT / "utc.txt", "utc", 0))
    
    '''
    if(onlyflash):
        print("operating in always on flash mode")
    '''
    
    
    #------- Setting up camera settings -------------
    
    '''
    #This is for getting min and max details for certain settings, (See the picam pdf manual)
    print(picam2.camera_controls["AnalogueGain"])
    min_gain, max_gain, default_gain = picam2.camera_controls["AnalogueGain"]
    '''
    #This will be the path to the CSV holding the settings whether it is the one on the disk or the external CSV
    global chosen_settings_path
    default_path = "/boot/firmware/mothbox_custom/camera_settings.csv"
    chosen_settings_path=default_path
    
    #camera_settings = load_camera_settings("camera_settings.csv")#CRONTAB CAN'T TAKE RELATIVE LINKS! 
    camera_settings = load_camera_settings()
    
        
    #before calibration, set these values to the default we read in
    
    
    calib_lens_position = float(read_control(AF_LENS_PATH, "aflensposition", 6.0))
    human_lens_position = camera_settings.get("LensPosition", None)
    
    calib_exposure      = float(read_control(AF_EXPOSURE_PATH, "exposuretime", 500))
    human_exposure = camera_settings["ExposureTime"]
    
    calib_gain          = float(read_control(AF_GAIN_PATH, "autogain", 1.0))
    
    AutoCalibration = camera_settings.pop("AutoCalibration",1) #defaults to what is set above if not in the files being read
    AutoCalibrationPeriod = int(camera_settings.pop("AutoCalibrationPeriod",1000))
    extract_af_limits(camera_settings)
    
    
    #Start up cameras
    # No camera (cable loose, ribbon backwards, dead module) is the one fault a
    # box in the field cannot show any other way while ACTIVE, so mirror the
    # Scheduler's standby warning: blink the lights 8 times, then give up on this
    # photo. Cron will try again next minute and warn again.
    def warn_no_camera(reason):
        """
        Give up on THIS photo, quietly.
    
        Deliberately does NOT blink the 8x warning or touch camera.txt. The box
        cannot have been unplugged mid-session, so a camera error here is almost
        always transient -- usually a previous TakePhoto still holding the camera
        ("Camera __init__ sequence did not complete"). On 2026-10-05 that fired 12
        times in one session on a healthy box: 12 spurious 8-blink alarms, ~96 s of
        extra full-brightness lights, and a false "CAMERA DISCONNECTED" banner.
        Detecting a genuinely absent camera is the Scheduler's job, once, at boot.
        """
        print(f"[TakePhoto] camera unavailable ({reason}) -- skipping this photo, will retry next cycle")
        if mothbox_hw is not None:
            try:
                mothbox_hw.flash_off()      # the flash must never be left on
            except Exception:
                pass
        else:
            run_cmd("python /home/pi/Desktop/Mothbox/Flash_Off.py")
        _disarm_flash_watchdog()
        sys.exit(0)
    
    # Whole-photo watchdog inside this process: if anything in the camera path
    # blocks (a camera that starts but never delivers a frame), give up, kill the
    # flash, warn. SIGALRM is delivered while picamera2 waits on Python-level
    # events, which is where these hangs happen.
    import signal
    PHOTO_WATCHDOG_S = max(150, int(get_burst_count() * get_burst_interval_s() + 60))
    
    def _photo_watchdog(signum, frame):
        warn_no_camera(f"photo did not complete within {PHOTO_WATCHDOG_S}s")
    
    signal.signal(signal.SIGALRM, _photo_watchdog)
    signal.alarm(PHOTO_WATCHDOG_S)
    
    # Picamera2() alone is enough. global_camera_info() used to run first, which
    # builds and tears down a whole CameraManager -- a second full libcamera
    # enumeration on every photo, for no benefit, since Picamera2() reports the
    # same failure.
    try:
        picam2 = Picamera2()
    except SystemExit:
        raise
    except Exception as e:
        warn_no_camera(f"{e.__class__.__name__}: {e}")
    
    def start_camera_or_warn(**kwargs):
        """
        picam2.start() that treats failure as "no camera". A ribbon that is
        unplugged after boot does NOT vanish from libcamera's list (the ov64a40
        overlay registered the sensor at boot, so Picamera2() still succeeds);
        the first real I2C / streaming access is what fails, with
        'Failed to start camera: Remote I/O error'. Every camera start in this
        file goes through here so that case blinks the warning too.
        """
        try:
            picam2.start(**kwargs)
        except Exception as e:
            warn_no_camera(f"camera failed to start: {e}")
    
    
    #----Autocalibration ---------
    
    current_time = int(time.time())
    timesincelastcalibration= current_time - LastCalibration
    print("Last calibration was   ",timesincelastcalibration,"  seconds ago \n Autocalibration period is   ", AutoCalibrationPeriod)
    recalibrated= False
    if AutoCalibration and (timesincelastcalibration > AutoCalibrationPeriod):
        print("Do Autocalibrate")
        recalibrated=True
        print(current_time)
        #picam2.configure(preview_config)
        #picam2.configure(capture_config_fastAuto)
        run_calibration()
    else:
        print("Don't Autocalibration")
    
    # ------ Prepare to take actual photo -----------
    #reload camera settings after possible calibration
    camera_settings = load_camera_settings()
    AutoCalibration = camera_settings.pop("AutoCalibration",1) #defaults to what is set above if not in the files being read
    AutoCalibrationPeriod = int(camera_settings.pop("AutoCalibrationPeriod",1000))
    extract_af_limits(camera_settings)
    
    if AutoCalibration:
        None
    else:
        calib_lens_position = human_lens_position
        calib_exposure = human_exposure
    
    
    #remove settings that aren't actually in picamera2
    oldsettingsnames = camera_settings.pop("Name",computerName) #defaults to what is set above if not in the files being read
    ImageFileType = int(camera_settings.pop("ImageFileType",0))
    _vf = camera_settings.pop("VerticalFlip", "auto")
    if str(_vf).strip().lower() in ("auto", ""):
        # Camera is mounted upside-down in the DIY enclosure, upright in the Pro.
        VerticalFlip = mothbox_hw.vertical_flip_default() if mothbox_hw else 0
    else:
        VerticalFlip = int(float(_vf))
    print(f"VerticalFlip: {VerticalFlip} (setting was {_vf!r})")
    onlyflash =int(camera_settings.pop("onlyflash",0))
    
    #HDR settings
    num_photos = int(camera_settings.pop("HDR",num_photos)) #defaults to what is set above if not in the files being read
    
    exposuretime_width = int(camera_settings.pop("HDR_width",exposuretime_width))
    if(num_photos<1 or num_photos==2):
        num_photos=1
    
    capture_main = {"size": (width, height), "format": "RGB888", }
    capture_config = picam2.create_still_configuration(main=capture_main,raw=None, lores=None)
    capture_config_flipped =  picam2.create_still_configuration(main=capture_main, transform=Transform(vflip=True, hflip=True), raw=None, lores=None)
    picam2.configure(capture_config)
    
    
    if camera_settings:
        print(camera_settings)
        print(calib_lens_position, calib_exposure, calib_gain)
        camera_settings["LensPosition"] = float(calib_lens_position)
        camera_settings["ExposureTime"] = int(calib_exposure)
        camera_settings["AnalogueGain"] = float(calib_gain)
        picam2.set_controls(camera_settings)
    
    start_camera_or_warn()
    time.sleep(1)
    
    print("cam started");
    
    picam2.stop()
    
    if(VerticalFlip):
        picam2.configure(capture_config_flipped)
    else:
        picam2.configure(capture_config)
    
    capture_mode = get_capture_mode()
    burst_count = get_burst_count()
    burst_interval_s = get_burst_interval_s()
    print(f"Capture mode: {capture_mode}")
    
    if capture_mode == "burst":
        takePhoto_Burst(burst_count=burst_count, interval_s=burst_interval_s)
    else:
        time.sleep(.5)
        takePhoto_Manual()
    
    
    picam2.stop()
    signal.alarm(0)
    _disarm_flash_watchdog()
    record_photo_taken()  # Only called on successful capture completion
    quit()
    
