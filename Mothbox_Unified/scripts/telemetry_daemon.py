#!/usr/bin/env python3
"""
Remote telemetry daemon that runs on the Mothbox and outputs structured,
unbuffered real-time updates for TakePhoto, Scheduler, and power metrics.
"""
import os
import sys
import time
import glob

sys.path.insert(0, "/home/pi/Desktop/Mothbox")
try:
    import mothbox_hw
except Exception:
    mothbox_hw = None

takephoto_log = "/home/pi/Desktop/Mothbox/logs/TakePhoto_log.txt"
scheduler_log = "/home/pi/Desktop/Mothbox/logs/Scheduler_log.txt"

tp_pos = os.path.getsize(takephoto_log) if os.path.exists(takephoto_log) else 0
sch_pos = os.path.getsize(scheduler_log) if os.path.exists(scheduler_log) else 0

# Show previous 10 lines of context on start
if os.path.exists(takephoto_log):
    try:
        with open(takephoto_log, "r", errors="replace") as f:
            lines = f.readlines()
            for line in lines[-10:]:
                line = line.strip()
                if line:
                    print(f"__MB_TP__{line}", flush=True)
    except Exception:
        pass

last_stat = 0

while True:
    now = time.time()
    if now - last_stat >= 3.0:
        last_stat = now
        is_flash = mothbox_hw.flash_is_on() if mothbox_hw else False
        if not is_flash:
            v, a = -1.0, 0.0
            if mothbox_hw:
                try:
                    p = mothbox_hw.read_power()
                    v = p.get("voltage_v", -1.0)
                    a = p.get("current_a", 0.0)
                except Exception:
                    pass
            photos = len(glob.glob("/home/pi/Desktop/Mothbox/photos/*/*.jpg")) + len(glob.glob("/home/pi/Desktop/Mothbox/photos/*.jpg"))
            mode = "STANDBY"
            try:
                with open("/var/lib/mothbox/state/mode.txt") as f:
                    mode = f.read().strip()
            except Exception:
                pass
            print(f"__MB_TEL__:{v:.2f}:{a:.3f}:{photos}:{mode}:IDLE", flush=True)

    if os.path.exists(takephoto_log):
        try:
            cur_sz = os.path.getsize(takephoto_log)
            if cur_sz > tp_pos:
                with open(takephoto_log, "r", errors="replace") as f:
                    f.seek(tp_pos)
                    lines = f.readlines()
                    tp_pos = f.tell()
                    for line in lines:
                        line = line.strip()
                        if line:
                            print(f"__MB_TP__{line}", flush=True)
            elif cur_sz < tp_pos:
                tp_pos = 0
        except Exception:
            pass

    if os.path.exists(scheduler_log):
        try:
            cur_sz = os.path.getsize(scheduler_log)
            if cur_sz > sch_pos:
                with open(scheduler_log, "r", errors="replace") as f:
                    f.seek(sch_pos)
                    lines = f.readlines()
                    sch_pos = f.tell()
                    for line in lines:
                        line = line.strip()
                        if line:
                            print(f"__MB_SCH__{line}", flush=True)
            elif cur_sz < sch_pos:
                sch_pos = 0
        except Exception:
            pass

    time.sleep(0.5)
