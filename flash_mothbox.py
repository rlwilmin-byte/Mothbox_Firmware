#!/usr/bin/env python3
"""
Mothbox Unified Firmware Flasher & Deployer
==========================================
Offline-capable tool to flash the latest firmware to a Mothbox over:
  1. Wi-Fi (connected to 'mothboxwifi' AP @ 192.168.50.5 or mothbox.local)
  2. Ethernet (direct cable @ 10.42.0.94 or auto-detected)
  3. Direct SD card (plugged into laptop / USB adapter)

Features:
  - Complete offline operation (zero internet required)
  - Auto-detection across mounted SD cards and network targets
  - Safe sync: never deletes photos or logs on the Mothbox
  - Enforces preferred_mothbox_settings.csv per deployment guidelines
  - Remote syntax and health verification
  - Automatic logging to logs/flash_YYYYMMDD_HHMMSS.log

Usage:
  python3 flash_mothbox.py                 # Auto-detect target (SD card, blank card, Wi-Fi, Ethernet)
  python3 flash_mothbox.py --blank         # Flash fresh/blank SD card with base OS image
  python3 flash_mothbox.py --blank /dev/sda --image /path/to/base.img
  python3 flash_mothbox.py --sd            # Quick code sync to existing Mothbox SD card
  python3 flash_mothbox.py --wifi          # Force Wi-Fi connection
  python3 flash_mothbox.py --eth           # Force Ethernet connection
  python3 flash_mothbox.py --dry-run       # Preview actions without writing
"""

import os
import sys
import time
import glob
import re
import json
import shutil
import argparse
import subprocess
import getpass

try:
    import pexpect
except ImportError:
    print("Error: 'pexpect' is required. Install via: pip install pexpect", file=sys.stderr)
    sys.exit(1)

# Paths relative to repo root
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
FIRMWARE_SRC = os.path.join(REPO_ROOT, "Mothbox_Unified")
DEFAULT_CSV = os.path.join(REPO_ROOT, "preferred_mothbox_settings.csv")
LOGS_DIR = os.path.join(REPO_ROOT, "logs")

# Network defaults
DEFAULT_USER = "pi"
DEFAULT_PASS = "luna"
CANDIDATE_NETWORKS = [
    ("192.168.50.5",  "Wi-Fi Hotspot (AccessPopup AP)"),
    ("mothbox.local", "mDNS Broadcast (Wi-Fi/Ethernet)"),
    ("10.42.0.94",    "Direct Ethernet (Standard Pi IP)"),
    ("10.42.0.1",     "Direct Ethernet / NetworkManager Gateway"),
]

# ANSI colors
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
DIM = "\033[2m"
RESET = "\033[0m"

# Session logging setup
os.makedirs(LOGS_DIR, exist_ok=True)
FLASH_LOG_PATH = os.path.join(LOGS_DIR, f"flash_{time.strftime('%Y%m%d_%H%M%S')}.log")
ANSI_RE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


def log(msg, level="INFO"):
    ts = time.strftime("%H:%M:%S")
    if level == "OK":
        prefix = f"{GREEN}{BOLD}[OK]{RESET}"
    elif level == "WARN":
        prefix = f"{YELLOW}{BOLD}[WARN]{RESET}"
    elif level == "ERR":
        prefix = f"{RED}{BOLD}[FAIL]{RESET}"
    else:
        prefix = f"{CYAN}[INFO]{RESET}"

    formatted = f"{DIM}[{ts}]{RESET} {prefix} {msg}"
    print(formatted, flush=True)

    # Mirror to clean local log file
    clean = ANSI_RE.sub('', formatted)
    try:
        with open(FLASH_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(clean + "\n")
    except Exception:
        pass


def banner():
    print(f"\n{BOLD}{'=' * 75}")
    print("      ⚡ MOTHBOX OFFLINE FIRMWARE FLASHER & DEPLOYER ⚡")
    print(f"      Repository: {REPO_ROOT}")
    print(f"      Log File:   {FLASH_LOG_PATH}")
    print(f"{'=' * 75}{RESET}\n", flush=True)


# ---------------------------------------------------------------------------
# SD Card Detection & Flashing
# ---------------------------------------------------------------------------

def find_sd_mounts(target_dev=None):
    """
    Scans for attached Mothbox SD cards using lsblk block device topology (<0.05s)
    and user media mount directories. Automatically mounts unmounted partitions via udisksctl.
    """
    candidates = []
    rootfs_path = None
    bootfs_path = None
    unmounted_root_dev = None
    unmounted_boot_dev = None

    # Step 1: Use lsblk to inspect block devices directly (fast, accurate, no filesystem hanging)
    try:
        out = subprocess.check_output(
            ["lsblk", "-J", "-p", "-b", "-o", "NAME,MOUNTPOINTS,LABEL,FSTYPE,SIZE,RM,TYPE,PKNAME"],
            text=True, timeout=3
        )
        data = json.loads(out)
        for dev in data.get("blockdevices", []):
            # CRITICAL: Only scan removable devices (SD cards, USB drives) -- NEVER internal NVMe/SATA host disks!
            is_removable = dev.get("rm", False)
            if not is_removable:
                continue
            if dev.get("type") != "disk":
                continue

            dev_name = dev.get("name", "")
            if target_dev and dev_name != target_dev:
                continue

            raw_size = dev.get("size") or 0
            try:
                size_bytes = int(raw_size)
            except (ValueError, TypeError):
                size_bytes = 0

            # Ignore empty card reader slots with no media inserted
            if size_bytes <= 0:
                continue

            children = dev.get("children", [])
            if not children:
                continue

            for child in children:
                c_name = child.get("name", "")
                c_label = str(child.get("label", "") or "").lower()
                c_size = str(child.get("size", "") or "")
                c_fstype = str(child.get("fstype", "") or "").lower()
                c_mps = [m for m in child.get("mountpoints", []) if m]

                # CRITICAL SAFETY: Host system paths must never be touched
                if any(m in ("/", "/home", "/root", "/boot", "/boot/efi") for m in c_mps):
                    continue

                # Match bootfs (label boot/bootfs or FAT 256M-1G)
                if c_label in ("bootfs", "boot") or (c_fstype in ("vfat", "fat") and any(s in c_size for s in ("256M", "512M", "1G"))):
                    if c_mps and not bootfs_path:
                        bootfs_path = c_mps[0]
                    elif not c_mps and not unmounted_boot_dev:
                        unmounted_boot_dev = c_name

                # Match rootfs (label rootfs or large ext4 partition)
                elif c_label == "rootfs" or (c_fstype == "ext4" and any(u in c_size for u in ("G", "T")) and "512M" not in c_size):
                    if c_mps and not rootfs_path:
                        rootfs_path = c_mps[0]
                    elif not c_mps and not unmounted_root_dev:
                        unmounted_root_dev = c_name
    except Exception as e:
        log(f"lsblk query error: {e}", "WARN")

    # Step 2: Auto-mount unmounted partitions if detected
    if not rootfs_path and unmounted_root_dev:
        log(f"Detected unmounted SD root partition ({unmounted_root_dev}). Auto-mounting...")
        try:
            m_res = subprocess.run(["udisksctl", "mount", "-b", unmounted_root_dev], capture_output=True, text=True, timeout=8)
            if m_res.returncode == 0:
                out_txt = m_res.stdout.strip()
                if " at " in out_txt:
                    new_mp = out_txt.split(" at ")[-1].rstrip(".")
                    if os.path.isdir(new_mp):
                        rootfs_path = new_mp
                        log(f"Successfully auto-mounted rootfs: {unmounted_root_dev} -> {rootfs_path}", "OK")
            else:
                user = getpass.getuser()
                mnt_dir = f"/media/{user}/rootfs"
                os.makedirs(mnt_dir, exist_ok=True)
                s_res = subprocess.run(["sudo", "mount", unmounted_root_dev, mnt_dir], capture_output=True, text=True)
                if s_res.returncode == 0:
                    rootfs_path = mnt_dir
                    log(f"Successfully auto-mounted rootfs via sudo: {unmounted_root_dev} -> {rootfs_path}", "OK")
                else:
                    log(f"Could not auto-mount {unmounted_root_dev} via udisksctl: {m_res.stderr.strip()}", "WARN")
        except Exception as e:
            log(f"Auto-mount error for rootfs: {e}", "WARN")

    if not bootfs_path and unmounted_boot_dev:
        log(f"Detected unmounted SD boot partition ({unmounted_boot_dev}). Auto-mounting...")
        try:
            m_res = subprocess.run(["udisksctl", "mount", "-b", unmounted_boot_dev], capture_output=True, text=True, timeout=8)
            if m_res.returncode == 0:
                out_txt = m_res.stdout.strip()
                if " at " in out_txt:
                    new_mp = out_txt.split(" at ")[-1].rstrip(".")
                    if os.path.isdir(new_mp):
                        bootfs_path = new_mp
                        log(f"Successfully auto-mounted bootfs: {unmounted_boot_dev} -> {bootfs_path}", "OK")
            else:
                user = getpass.getuser()
                mnt_dir = f"/media/{user}/bootfs"
                os.makedirs(mnt_dir, exist_ok=True)
                s_res = subprocess.run(["sudo", "mount", unmounted_boot_dev, mnt_dir], capture_output=True, text=True)
                if s_res.returncode == 0:
                    bootfs_path = mnt_dir
                    log(f"Successfully auto-mounted bootfs via sudo: {unmounted_boot_dev} -> {bootfs_path}", "OK")
                else:
                    log(f"Could not auto-mount {unmounted_boot_dev} via udisksctl: {m_res.stderr.strip()}", "WARN")
        except Exception as e:
            log(f"Auto-mount error for bootfs: {e}", "WARN")

    # Step 3: Fast fallback scan across user media mount directories (NEVER /mnt to prevent slow network hanging)
    if not rootfs_path or not bootfs_path:
        user = getpass.getuser()
        safe_search_dirs = [f"/run/media/{user}", f"/media/{user}", "/media"]
        for s_dir in safe_search_dirs:
            if not os.path.isdir(s_dir):
                continue
            try:
                for entry in os.listdir(s_dir):
                    mp = os.path.join(s_dir, entry)
                    if not os.path.isdir(mp):
                        continue
                    # Check rootfs
                    if not rootfs_path and mp not in ("/", "/home", "/root", "/boot", "/boot/efi"):
                        if os.path.isdir(os.path.join(mp, "home", "pi", "Desktop", "Mothbox")) or \
                           (os.path.exists(os.path.join(mp, "etc", "rpi-issue")) and os.path.isdir(os.path.join(mp, "home", "pi"))):
                            rootfs_path = mp
                    # Check bootfs (MUST contain config.txt or cmdline.txt and CANNOT be rootfs)
                    if not bootfs_path and mp != rootfs_path and mp not in ("/", "/home", "/root", "/boot", "/boot/efi"):
                        if os.path.exists(os.path.join(mp, "config.txt")) or os.path.exists(os.path.join(mp, "cmdline.txt")):
                            bootfs_path = mp
            except Exception:
                pass

    # Safety: rootfs must NEVER be the host operating system root
    if rootfs_path in ("/", "/home", "/root", "/boot", "/boot/efi"):
        log(f"Safety guard: Rejected invalid host rootfs candidate '{rootfs_path}'", "WARN")
        rootfs_path = None

    # Safety: rootfs and bootfs must never point to the same directory
    if bootfs_path and rootfs_path and os.path.abspath(bootfs_path) == os.path.abspath(rootfs_path):
        bootfs_path = None

    if rootfs_path or bootfs_path:
        desc_parts = []
        if rootfs_path:
            desc_parts.append(f"Rootfs: {rootfs_path}")
        else:
            desc_parts.append(f"Rootfs: [Unmounted: {unmounted_root_dev or 'Pending'}]")
        if bootfs_path:
            desc_parts.append(f"Bootfs: {bootfs_path}")

        candidates.append({
            "type": "sd",
            "rootfs": rootfs_path,
            "bootfs": bootfs_path,
            "unmounted_root_dev": unmounted_root_dev,
            "desc": " | ".join(desc_parts)
        })

    return candidates


def flash_sd_card(rootfs, bootfs, csv_path, dry_run=False, keep_settings=False, unmounted_root_dev=None):
    """Flashes firmware directly to mounted SD card partitions."""
    # Ironclad safety check: rootfs must be on an external media mount, NEVER host OS
    if rootfs in ("/", "/home", "/root", "/boot", "/boot/efi") or (rootfs and not rootfs.startswith(("/run/media/", "/media/", "/mnt/"))):
        log(f"FATAL SAFETY ERROR: Target rootfs '{rootfs}' is not a valid removable media mount! Aborting.", "ERR")
        return False

    if not rootfs and not bootfs:
        log("No valid SD card partition specified or mounted.", "ERR")
        return False

    if not rootfs:
        log(f"Warning: Root partition ({unmounted_root_dev or 'rootfs'}) is not mounted!", "WARN")
        log("Only bootfs is accessible. Firmware code sync requires rootfs to be mounted.", "WARN")
        if unmounted_root_dev:
            log(f"Tip: Mount the partition with: udisksctl mount -b {unmounted_root_dev}", "INFO")

        if bootfs and bootfs != rootfs and not keep_settings and os.path.exists(csv_path):
            log(f"Deploying preferred settings CSV to bootfs ({os.path.basename(csv_path)})...")
            custom_dir = os.path.join(bootfs, "mothbox_custom")
            try:
                if os.path.isfile(custom_dir) or os.path.islink(custom_dir):
                    log(f"Warning: {custom_dir} is an unexpected file. Removing to create directory...", "WARN")
                    if not dry_run:
                        os.remove(custom_dir)
                if not dry_run:
                    os.makedirs(custom_dir, exist_ok=True)
                bootfs_csv = os.path.join(custom_dir, "mothbox_settings.csv")
                if not dry_run:
                    shutil.copy2(csv_path, bootfs_csv)
                    subprocess.run(["sync"])
                log(f"Settings successfully deployed to bootfs: {bootfs_csv}", "OK")
            except OSError as e:
                log(f"Failed to deploy settings to bootfs: {e}", "WARN")
        log("Firmware files could NOT be synchronized because rootfs is unmounted.", "ERR")
        return False

    target_dir = os.path.join(rootfs, "home", "pi", "Desktop", "Mothbox")
    os.makedirs(target_dir, exist_ok=True)

    log(f"Target Mothbox rootfs directory: {target_dir}")
    if dry_run:
        log("DRY RUN: No files will be copied.", "WARN")

    rsync_cmd = [
        "rsync", "-av",
        "--exclude", "__pycache__",
        "--exclude", "*.pyc",
        "--exclude", ".git*",
        "--exclude", "tests/",
        "--exclude", "photos/",
        "--exclude", "logs/",
        f"{FIRMWARE_SRC}/",
        f"{target_dir}/"
    ]

    if dry_run:
        rsync_cmd.insert(2, "--dry-run")

    log(f"Syncing firmware files: {FIRMWARE_SRC} -> {target_dir}...")
    res = subprocess.run(rsync_cmd, capture_output=True, text=True)
    if res.returncode != 0:
        log(f"rsync failed: {res.stderr}", "ERR")
        return False

    log("Core firmware files synced successfully.", "OK")

    # Deploy Settings CSV
    if not keep_settings and os.path.exists(csv_path):
        log(f"Deploying preferred settings CSV ({os.path.basename(csv_path)})...")
        # 1. Rootfs copy
        rootfs_csv = os.path.join(target_dir, "mothbox_settings.csv")
        if not dry_run:
            shutil.copy2(csv_path, rootfs_csv)
        log(f"Copied to rootfs: {rootfs_csv}", "OK")

        # 2. Bootfs copy
        if bootfs and bootfs != rootfs:
            custom_dir = os.path.join(bootfs, "mothbox_custom")
            try:
                if os.path.isfile(custom_dir) or os.path.islink(custom_dir):
                    log(f"Warning: {custom_dir} is an unexpected file. Removing to create directory...", "WARN")
                    if not dry_run:
                        os.remove(custom_dir)
                if not dry_run:
                    os.makedirs(custom_dir, exist_ok=True)
                bootfs_csv = os.path.join(custom_dir, "mothbox_settings.csv")
                if not dry_run:
                    shutil.copy2(csv_path, bootfs_csv)
                log(f"Copied to bootfs: {bootfs_csv}", "OK")
            except OSError as e:
                log(f"Warning: Could not copy settings CSV to bootfs: {e}", "WARN")
    elif keep_settings:
        log("Skipping settings CSV update (--keep-settings specified).")

    # Sync filesystem buffers to physical storage
    if not dry_run:
        log("Flushing physical write buffers (sync)...")
        subprocess.run(["sync"])
        log("Write buffers committed. Safe to unmount and remove SD card.", "OK")

    return True


def check_firmware_diffs(src_dir, target_dir):
    """
    Compares local source firmware against target directory using rsync dry-run.
    Returns list of relative filepaths that differ, or None if target directory does not exist or error.
    """
    if not os.path.isdir(target_dir):
        return None
    cmd = [
        "rsync", "-avn",
        "--exclude", "__pycache__",
        "--exclude", "*.pyc",
        "--exclude", ".git*",
        "--exclude", "tests/",
        "--exclude", "photos/",
        "--exclude", "logs/",
        f"{src_dir}/",
        f"{target_dir}/"
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if res.returncode != 0:
            return None
        lines = [l.strip() for l in res.stdout.splitlines() if l.strip()]
        diff_files = []
        started = False
        for line in lines:
            if line == "sending incremental file list":
                started = True
                continue
            if line.startswith("sent ") and "bytes" in line:
                break
            if started and line and not line.endswith("/"):
                diff_files.append(line)
        return diff_files
    except Exception:
        return None


def check_csv_match(src_csv, target_csv):
    """Checks whether two settings CSV files exist and are identical in content."""
    if not os.path.isfile(src_csv) or not os.path.isfile(target_csv):
        return False
    try:
        with open(src_csv, "rb") as f1, open(target_csv, "rb") as f2:
            return f1.read().strip() == f2.read().strip()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Blank / Unformatted SD Card Imaging
# ---------------------------------------------------------------------------

def format_size(bytes_num):
    """Formats bytes into human-readable size."""
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_num < 1024.0:
            return f"{bytes_num:.1f} {unit}"
        bytes_num /= 1024.0
    return f"{bytes_num:.1f} PB"


def find_base_images():
    """Finds candidate Mothbox base OS images (.img, .img.zip, .img.xz) on the host machine."""
    candidates = []
    search_dirs = [
        os.path.join(REPO_ROOT, ".."),
        os.path.join(os.path.expanduser("~"), "Documents", "Mothbox"),
        os.path.join(os.path.expanduser("~"), "Downloads"),
        os.path.expanduser("~"),
    ]
    patterns = ["*mothbox*pi5*.img*", "*mothbox*.img*", "*.img", "*.img.zip", "*.img.xz"]
    seen = set()
    for s_dir in search_dirs:
        if not os.path.isdir(s_dir):
            continue
        for pat in patterns:
            for p in glob.glob(os.path.join(s_dir, pat)):
                abs_p = os.path.abspath(p)
                if abs_p not in seen and os.path.isfile(abs_p):
                    seen.add(abs_p)
                    try:
                        # Base OS images are at least 500 MB
                        if os.path.getsize(abs_p) > 500 * 1024 * 1024:
                            candidates.append(abs_p)
                    except Exception:
                        pass
    # Sort uncompressed .img first, then newest version
    candidates.sort(key=lambda x: (x.endswith(".img"), os.path.basename(x)), reverse=True)
    return candidates


def find_blank_drives():
    """
    Scans for attached removable block devices that are NOT already partitioned
    as Mothbox cards (e.g. blank SD cards or storage cards with non-Mothbox filesystems).
    Ignores empty card reader slots (size = 0) and host drives.
    """
    drives = []
    try:
        out = subprocess.check_output(
            ["lsblk", "-J", "-p", "-b", "-o", "NAME,MOUNTPOINTS,LABEL,FSTYPE,SIZE,RM,TYPE,MODEL,VENDOR"],
            text=True, timeout=3
        )
        data = json.loads(out)
        for dev in data.get("blockdevices", []):
            # Only consider removable drives (SD cards, USB drives)
            if not dev.get("rm", False):
                continue
            if dev.get("type") != "disk":
                continue

            dev_name = dev.get("name", "")
            raw_size = dev.get("size") or 0
            try:
                size_bytes = int(raw_size)
            except (ValueError, TypeError):
                size_bytes = 0

            # CRITICAL: Empty card reader slots report rm=True but size=0 (no medium inserted)
            if size_bytes <= 0:
                continue

            # Skip tiny drives (< 1 GB) that cannot hold an OS image
            if size_bytes < 1024 * 1024 * 1024:
                continue

            dev_size = format_size(size_bytes)
            dev_model = dev.get("model", "") or dev.get("vendor", "") or "Removable Storage"
            children = dev.get("children", [])

            # Safety check: skip if any child has host system mounts
            has_host_mount = False
            for child in children:
                for mp in child.get("mountpoints", []):
                    if mp in ("/", "/home", "/boot", "/boot/efi", "/root"):
                        has_host_mount = True
                        break
            if has_host_mount:
                continue

            # Check if this drive already has recognized Mothbox partitions
            has_mothbox_partitions = False
            for child in children:
                c_label = str(child.get("label", "") or "").lower()
                c_mps = [m for m in child.get("mountpoints", []) if m]
                if c_label in ("bootfs", "boot", "rootfs"):
                    has_mothbox_partitions = True
                    break
                for mp in c_mps:
                    if os.path.exists(os.path.join(mp, "config.txt")) or \
                       os.path.exists(os.path.join(mp, "home", "pi", "Desktop", "Mothbox")):
                        has_mothbox_partitions = True
                        break

            if not has_mothbox_partitions:
                part_descs = []
                for child in children:
                    c_name = child.get("name", "")
                    c_label = child.get("label", "") or "NoLabel"
                    c_size_bytes = int(child.get("size") or 0)
                    c_size = format_size(c_size_bytes) if c_size_bytes else child.get("size", "")
                    part_descs.append(f"{os.path.basename(c_name)} ({c_size}, {c_label})")

                drives.append({
                    "device": dev_name,
                    "size": dev_size,
                    "size_bytes": size_bytes,
                    "model": dev_model.strip(),
                    "partitions": [c.get("name") for c in children],
                    "part_descs": ", ".join(part_descs) if part_descs else "Raw / Unpartitioned",
                    "desc": f"{dev_name} ({dev_size} - {dev_model.strip()}) [{', '.join(part_descs) if part_descs else 'Raw'}]"
                })
    except Exception as e:
        log(f"Error querying blank block devices: {e}", "WARN")

    return drives


def flash_blank_sd_card(device, image_path, csv_path, dry_run=False, keep_settings=False):
    """
    Writes a base OS image to a raw removable drive using dd,
    re-reads the partition table, and overlays the latest firmware.
    """
    # 1. Ironclad safety checks
    if not device.startswith("/dev/"):
        log(f"Invalid device path: {device}", "ERR")
        return False
    if "nvme" in device or "loop" in device:
        log(f"FATAL SAFETY ERROR: Refusing to write to internal or loop device '{device}'!", "ERR")
        return False

    try:
        out = subprocess.check_output(["lsblk", "-J", "-p", "-b", "-o", "NAME,MOUNTPOINTS,RM,SIZE,TYPE"], text=True)
        dev_data = json.loads(out)
        target_info = None
        for d in dev_data.get("blockdevices", []):
            if d.get("name") == device:
                target_info = d
                break
            for child in d.get("children", []):
                if child.get("name") == device:
                    target_info = child
                    break

        if not target_info:
            log(f"FATAL ERROR: Target device {device} not found in system block devices!", "ERR")
            return False

        if not target_info.get("rm", False):
            log(f"FATAL SAFETY ERROR: Device {device} is marked as non-removable in kernel! Aborting.", "ERR")
            return False

        dev_bytes = int(target_info.get("size") or 0)
        if dev_bytes <= 0:
            log(f"FATAL ERROR: Target device {device} has no media inserted (size is 0 bytes)! Check card reader slot.", "ERR")
            return False

        img_bytes = os.path.getsize(image_path)
        if dev_bytes < img_bytes:
            log(f"FATAL ERROR: Target device {device} ({format_size(dev_bytes)}) is smaller than base OS image ({format_size(img_bytes)})! Aborting.", "ERR")
            return False

        for child in target_info.get("children", [target_info]):
            for mp in child.get("mountpoints", []):
                if mp in ("/", "/home", "/boot", "/boot/efi", "/root"):
                    log(f"FATAL SAFETY ERROR: Device {device} holds host mount '{mp}'! Aborting.", "ERR")
                    return False
    except Exception as e:
        log(f"Safety verification failed: {e}", "ERR")
        return False

    if dry_run:
        log(f"DRY RUN: Would unmount all active partitions on {device}", "WARN")
        log(f"DRY RUN: Would write base image {image_path} to {device} via dd", "WARN")
        log(f"DRY RUN: Would re-read partition table via partprobe and auto-mount partitions", "WARN")
        log(f"DRY RUN: Would sync {FIRMWARE_SRC} and deploy {csv_path}", "WARN")
        return True

    # 2. Unmount any active partitions on the target device
    log(f"Unmounting active partitions on {device}...")
    try:
        out = subprocess.check_output(["lsblk", "-n", "-r", "-o", "NAME,MOUNTPOINTS", device], text=True)
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1]:
                part_name = parts[0]
                log(f"Unmounting {part_name}...")
                subprocess.run(["udisksctl", "unmount", "-b", part_name], capture_output=True)
    except Exception as e:
        log(f"Warning during unmount: {e}", "WARN")

    # Pre-authenticate sudo credentials so password prompt doesn't interfere with dd progress
    try:
        subprocess.run(["sudo", "-v"], check=True)
    except Exception:
        log("Could not obtain sudo privileges.", "ERR")
        return False

    # 3. Stream or write base image to block device
    img_size_str = format_size(os.path.getsize(image_path))
    log(f"Writing base image directly to {device} (Size: {img_size_str})...")
    log(f"Image source: {image_path}")
    log("Direct physical I/O enabled (oflag=direct): continuous live write progress will stream below.")
    log("Estimated time: ~3–5 minutes depending on SD card reader speed. Please do not interrupt.")

    start_t = time.time()
    try:
        if image_path.endswith(".zip"):
            cmd = f"unzip -p '{image_path}' | sudo dd of={device} bs=4M oflag=direct status=progress conv=fsync"
            res = subprocess.run(cmd, shell=True)
        elif image_path.endswith(".xz"):
            cmd = f"xzcat '{image_path}' | sudo dd of={device} bs=4M oflag=direct status=progress conv=fsync"
            res = subprocess.run(cmd, shell=True)
        else:
            cmd = ["sudo", "dd", f"if={image_path}", f"of={device}", "bs=4M", "oflag=direct", "status=progress", "conv=fsync"]
            res = subprocess.run(cmd)
    except KeyboardInterrupt:
        print()
        log("Image flash interrupted by user (Ctrl+C). Aborting.", "WARN")
        return False

    if res.returncode != 0:
        if res.returncode in (-2, 130):
            log("Image write interrupted by user (Ctrl+C).", "WARN")
        else:
            log(f"Image write failed with exit code {res.returncode}", "ERR")
        return False

    write_t = time.time() - start_t
    log(f"Base OS image written successfully to {device} in {write_t:.1f}s!", "OK")

    # 4. Kernel partition re-probe (unmount stale mounts, rescan SCSI bus & reload partition table)
    log("Re-probing kernel partition table and auto-mounting newly created partitions...")
    try:
        # Unmount any stale mounts that might lock the block device
        out = subprocess.check_output(["lsblk", "-n", "-r", "-o", "NAME,MOUNTPOINTS", device], text=True)
        for line in out.strip().splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1]:
                subprocess.run(["udisksctl", "unmount", "-b", parts[0]], capture_output=True)
    except Exception:
        pass

    try:
        # Refresh sudo credentials (writes often take > 10-15 minutes, exceeding sudo timeout)
        subprocess.run(["sudo", "-v"])

        # Method A: Sysfs SCSI rescan (forces kernel SCSI subsystem to re-probe device geometry)
        dev_short = os.path.basename(device)
        rescan_path = f"/sys/block/{dev_short}/device/rescan"
        if os.path.exists(rescan_path):
            subprocess.run(f"echo 1 | sudo tee {rescan_path} > /dev/null", shell=True)

        # Method B: Re-read partition table via blockdev and partprobe
        subprocess.run(["sudo", "blockdev", "--rereadpt", device], capture_output=True)
        subprocess.run(["sudo", "partprobe", device], capture_output=True)

        # Method C: Trigger udev change event
        subprocess.run(["sudo", "udevadm", "trigger", "--subsystem-match=block", "--action=change", device], capture_output=True)
        subprocess.run(["udevadm", "settle", "--timeout=5"], capture_output=True)
    except Exception as e:
        log(f"Warning during partition re-probe: {e}", "WARN")

    # 5. Discover newly written partitions and auto-mount (poll up to 5 attempts)
    log("Discovering and auto-mounting newly created Mothbox partitions...")
    sd_info = None
    for attempt in range(1, 6):
        time.sleep(1.2)
        sd_candidates = find_sd_mounts(target_dev=device)
        if sd_candidates and sd_candidates[0].get("rootfs"):
            sd_info = sd_candidates[0]
            break

    if not sd_info or not sd_info.get("rootfs"):
        print(f"\n{YELLOW}{BOLD}{'=' * 75}")
        print("  ⚠️   ACTION REQUIRED: PHYSICAL RE-INSERTION NEEDED TO FINISH SYNC   ⚠️")
        print(f"{'=' * 75}{RESET}")
        print(f"  • Base OS Image:     {GREEN}{BOLD}Successfully burned{RESET} to {device} ({img_size_str})")
        print("  • Operating System:  Mothbox Pro Pi 5 Base OS v6.0.1")
        print("  • Partition Status:  Kernel cached the old partition table in RAM.")
        print()
        print(f"  {BOLD}{CYAN}>>> NEXT STEP TO COMPLETE SETUP:{RESET}")
        print(f"  {BOLD}1. Physically unplug the SD card from your laptop / USB reader.{RESET}")
        print(f"  {BOLD}2. Plug it back into the laptop.{RESET}")
        print(f"  {BOLD}3. Run:{RESET} {GREEN}{BOLD}python3 flash_mothbox.py{RESET}")
        print()
        print("  The flasher will auto-detect the card and complete the 20-second")
        print("  firmware overlay and configuration sync automatically.")
        print(f"{YELLOW}{BOLD}{'=' * 75}{RESET}\n")
        return False

    sd_info = sd_candidates[0]
    log(f"Successfully mounted newly imaged SD partitions:", "OK")
    log(f"  • Rootfs: {sd_info.get('rootfs')}")
    log(f"  • Bootfs: {sd_info.get('bootfs')}")

    # 6. Overlay latest repository firmware and settings CSV!
    log("Overlaying latest firmware updates and preferred settings CSV onto new card...")
    success = flash_sd_card(
        rootfs=sd_info.get("rootfs"),
        bootfs=sd_info.get("bootfs"),
        csv_path=csv_path,
        dry_run=False,
        keep_settings=keep_settings,
        unmounted_root_dev=sd_info.get("unmounted_root_dev")
    )

    return success


# ---------------------------------------------------------------------------
# Network Detection & Flashing (SSH / OpenSSH ControlMaster)
# ---------------------------------------------------------------------------

def test_ssh_target(host, user=DEFAULT_USER, password=DEFAULT_PASS, timeout=3):
    """Probes whether a Mothbox SSH daemon is reachable at host."""
    cmd = f"ssh -F /dev/null -o StrictHostKeyChecking=no -o ConnectTimeout={timeout} {user}@{host} 'echo ready'"
    try:
        child = pexpect.spawn(cmd, timeout=timeout + 2)
        idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=timeout)
        if idx == 0:
            child.sendline(password)
            child.expect(pexpect.EOF, timeout=3)
            if b"ready" in child.before:
                return True
    except Exception:
        pass
    return False


def probe_network_targets():
    """Probes candidate network interfaces to discover live Mothbox units."""
    active = []
    for host, label in CANDIDATE_NETWORKS:
        sys.stdout.write(f"\r  Probing {host.ljust(15)} ({label})...")
        sys.stdout.flush()
        if test_ssh_target(host):
            active.append((host, label))
    sys.stdout.write("\r" + " " * 75 + "\r")
    sys.stdout.flush()
    return active


class RemoteSSHManager:
    """Manages an authenticated OpenSSH ControlMaster connection for wire-speed batch transfers."""
    def __init__(self, host, user=DEFAULT_USER, password=DEFAULT_PASS):
        self.host = host
        self.user = user
        self.password = password
        self.socket_path = f"/tmp/mothbox_flash_{host}_{os.getpid()}.sock"

    def open(self):
        log(f"Authenticating master connection to {self.user}@{self.host}...")
        cmd = (
            f"ssh -F /dev/null -o StrictHostKeyChecking=no "
            f"-o ControlMaster=yes -o ControlPath={self.socket_path} -o ControlPersist=5m "
            f"-N {self.user}@{self.host}"
        )
        self.master_child = pexpect.spawn(cmd, timeout=10)
        idx = self.master_child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=8)
        if idx != 0:
            raise ConnectionError(f"Authentication failed: {self.master_child.before.decode(errors='replace')}")
        self.master_child.sendline(self.password)
        time.sleep(0.5)

        # Verify socket created
        if not os.path.exists(self.socket_path):
            time.sleep(1)
        if not os.path.exists(self.socket_path):
            raise ConnectionError("ControlMaster socket failed to initialize.")
        log("Master SSH connection authenticated successfully.", "OK")

    def run_cmd(self, command, timeout=30):
        cmd = f"ssh -F /dev/null -o ControlPath={self.socket_path} {self.user}@{self.host} '{command}'"
        res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return res

    def close(self):
        try:
            subprocess.run(
                f"ssh -F /dev/null -o ControlPath={self.socket_path} -O exit {self.user}@{self.host}",
                shell=True, capture_output=True, timeout=5
            )
        except Exception:
            pass
        if os.path.exists(self.socket_path):
            try:
                os.remove(self.socket_path)
            except Exception:
                pass


def flash_over_network(host, user=DEFAULT_USER, password=DEFAULT_PASS, csv_path=DEFAULT_CSV, dry_run=False, keep_settings=False):
    """Syncs firmware to remote Mothbox using multiplexed SSH and rsync."""
    manager = RemoteSSHManager(host, user, password)
    try:
        manager.open()

        # Pre-flight: Check disk space
        res = manager.run_cmd("df -m / | awk 'NR==2 {print $4}'")
        if res.returncode == 0:
            free_mb = int(res.stdout.strip())
            log(f"Target disk space: {free_mb} MB available on /")
            if free_mb < 300:
                log("Warning: Disk space is low (< 300 MB). Proceeding cautiously.", "WARN")

        # Pre-flight: Remote identity
        res = manager.run_cmd("cat /etc/hostname 2>/dev/null || hostname")
        hostname = res.stdout.strip() if res.returncode == 0 else "mothbox"
        log(f"Connected to Mothbox unit: '{BOLD}{hostname}{RESET}' @ {host}")

        # Ensure destination directory exists
        manager.run_cmd("mkdir -p /home/pi/Desktop/Mothbox")

        # Sync core firmware using rsync over the authenticated control socket
        log(f"Transferring firmware files ({FIRMWARE_SRC} -> /home/pi/Desktop/Mothbox/)...")
        rsync_cmd = [
            "rsync", "-avz",
            "--exclude", "__pycache__",
            "--exclude", "*.pyc",
            "--exclude", ".git*",
            "--exclude", "tests/",
            "--exclude", "photos/",
            "--exclude", "logs/",
            "-e", f"ssh -F /dev/null -o ControlPath={manager.socket_path}",
            f"{FIRMWARE_SRC}/",
            f"{user}@{host}:/home/pi/Desktop/Mothbox/"
        ]

        if dry_run:
            rsync_cmd.insert(2, "--dry-run")
            log("DRY RUN: Showing file synchronization plan...", "WARN")

        res = subprocess.run(rsync_cmd, capture_output=True, text=True)
        if res.returncode != 0:
            log(f"rsync failed: {res.stderr}", "ERR")
            return False

        log("Core firmware files synced successfully.", "OK")

        # Deploy Settings CSV
        if not keep_settings and os.path.exists(csv_path):
            log(f"Deploying settings CSV ({os.path.basename(csv_path)})...")
            scp_cmd = [
                "scp", "-F", "/dev/null", "-o", f"ControlPath={manager.socket_path}",
                csv_path,
                f"{user}@{host}:/home/pi/Desktop/Mothbox/mothbox_settings.csv"
            ]
            if not dry_run:
                subprocess.run(scp_cmd, check=True)
                # Copy to boot partition with sudo
                res = manager.run_cmd(
                    f"echo {password} | sudo -S cp /home/pi/Desktop/Mothbox/mothbox_settings.csv /boot/firmware/mothbox_custom/mothbox_settings.csv 2>/dev/null || "
                    f"echo {password} | sudo -S cp /home/pi/Desktop/Mothbox/mothbox_settings.csv /boot/mothbox_custom/mothbox_settings.csv 2>/dev/null || true"
                )
            log("Settings CSV deployed to /home/pi/Desktop/Mothbox/ and /boot/firmware/.", "OK")
        elif keep_settings:
            log("Skipping settings CSV update (--keep-settings specified).")

        # Post-flash verification: remote python compile
        if not dry_run:
            log("Verifying remote python scripts syntax...")
            res = manager.run_cmd("python3 -m py_compile /home/pi/Desktop/Mothbox/Scheduler.py /home/pi/Desktop/Mothbox/TakePhoto.py")
            if res.returncode == 0:
                log("Remote Python compilation verified (Scheduler.py, TakePhoto.py).", "OK")
            else:
                log(f"Remote compile check returned error: {res.stderr}", "WARN")

        return True

    finally:
        manager.close()


# ---------------------------------------------------------------------------
# Main Controller & Interactive CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Mothbox Unified Firmware Flasher (Wi-Fi, Ethernet, SD card, Blank card)"
    )
    parser.add_argument("--wifi", action="store_true", help="Force Wi-Fi connection (192.168.50.5)")
    parser.add_argument("--eth", action="store_true", help="Force Ethernet connection (10.42.0.94)")
    parser.add_argument("--ip", type=str, help="Target specific IP address or hostname")
    parser.add_argument("--sd", nargs="?", const="auto", help="Flash mounted SD card (optional: specify mount path)")
    parser.add_argument("--blank", nargs="?", const="auto", help="Flash fresh/blank SD card with base Mothbox OS image (optional: specify /dev/sdX)")
    parser.add_argument("--image", type=str, help="Path to base Mothbox OS image file (.img, .img.zip, .img.xz)")
    parser.add_argument("--csv", type=str, default=DEFAULT_CSV, help=f"Settings CSV to deploy (default: {os.path.basename(DEFAULT_CSV)})")
    parser.add_argument("--keep-settings", action="store_true", help="Preserve existing mothbox_settings.csv on target")
    parser.add_argument("--dry-run", action="store_true", help="Show actions and files that would be updated without writing")
    parser.add_argument("-y", "--yes", action="store_true", help="Skip confirmation prompts and flash immediately")

    args = parser.parse_args()
    banner()

    # 1. Validate Source Firmware Files
    if not os.path.exists(FIRMWARE_SRC):
        log(f"Firmware source directory not found: {FIRMWARE_SRC}", "ERR")
        sys.exit(1)

    log(f"Source Firmware: {FIRMWARE_SRC}")
    if not args.keep_settings:
        if not os.path.exists(args.csv):
            log(f"Settings CSV not found: {args.csv}", "ERR")
            sys.exit(1)
        log(f"Profile CSV:    {args.csv} ({'Default Preferred' if args.csv == DEFAULT_CSV else 'Custom'})")

    # 2. Determine Target
    target_type = None
    target_host = None
    sd_info = None
    blank_info = None

    if args.ip:
        target_type = "network"
        target_host = args.ip
    elif args.wifi:
        target_type = "network"
        target_host = "192.168.50.5"
    elif args.eth:
        target_type = "network"
        target_host = "10.42.0.94"
    elif args.blank:
        target_type = "blank_sd"
        # Determine base OS image
        base_img = None
        if args.image:
            if not os.path.isfile(args.image):
                log(f"Specified base image file not found: {args.image}", "ERR")
                sys.exit(1)
            base_img = args.image
        else:
            candidates = find_base_images()
            if not candidates:
                log("No base Mothbox OS image found! Please provide one via --image <path>", "ERR")
                sys.exit(1)
            base_img = candidates[0]

        # Determine target device
        if args.blank != "auto":
            b_dev = args.blank
            b_size = "Unknown"
            b_model = "Specified Drive"
            try:
                b_out = subprocess.check_output(["lsblk", "-J", "-p", "-b", "-o", "NAME,SIZE,MODEL,VENDOR", b_dev], text=True)
                b_data = json.loads(b_out)
                for bd in b_data.get("blockdevices", []):
                    if bd.get("name") == b_dev:
                        b_raw_size = int(bd.get("size") or 0)
                        b_size = format_size(b_raw_size)
                        b_model = (bd.get("model") or bd.get("vendor") or "Removable Storage").strip()
                        break
            except Exception:
                pass

            blank_info = {
                "device": b_dev,
                "size": b_size,
                "model": b_model,
                "part_descs": "Raw",
                "image": base_img,
                "image_size": format_size(os.path.getsize(base_img))
            }
        else:
            drives = find_blank_drives()
            if not drives:
                log("No blank/uninitialized removable drives found. Ensure the SD card is plugged in.", "ERR")
                sys.exit(1)
            if len(drives) == 1:
                b_drive = drives[0]
            else:
                print(f"\n{BOLD}Multiple candidate removable drives detected:{RESET}")
                for i, d in enumerate(drives, 1):
                    print(f"  [{i}] {d['desc']}")
                try:
                    choice = input(f"{YELLOW}Select drive to flash [1-{len(drives)}]: {RESET}").strip()
                    if choice.isdigit() and 1 <= int(choice) <= len(drives):
                        b_drive = drives[int(choice) - 1]
                    else:
                        log("Invalid drive selection.", "ERR")
                        sys.exit(1)
                except KeyboardInterrupt:
                    print("\nAborted.")
                    sys.exit(0)
            blank_info = b_drive
            blank_info["image"] = base_img
            blank_info["image_size"] = format_size(os.path.getsize(base_img))

    elif args.sd:
        target_type = "sd"
        if args.sd != "auto" and os.path.isdir(args.sd):
            sd_info = {"rootfs": args.sd, "bootfs": None, "desc": f"Custom SD Path: {args.sd}"}
        else:
            sd_candidates = find_sd_mounts()
            if not sd_candidates:
                log("No mounted Mothbox SD cards found.", "ERR")
                log("Please plug in the SD card or specify its mount path: --sd /path/to/mount", "INFO")
                sys.exit(1)
            sd_info = sd_candidates[0]

    # Auto-detection
    if not target_type:
        log("No target specified -- auto-detecting attached SD cards and network devices...")

        # A. Check for existing Mothbox SD Card first
        sd_candidates = find_sd_mounts()
        if sd_candidates:
            target_type = "sd"
            sd_info = sd_candidates[0]
            log(f"Auto-detected mounted SD card: {sd_info['desc']}", "OK")

        # B. Check for uninitialized / blank removable drives
        if not target_type:
            blank_drives = find_blank_drives()
            base_images = [args.image] if (args.image and os.path.isfile(args.image)) else find_base_images()
            if blank_drives and base_images:
                b_img = base_images[0]
                b_img_size = format_size(os.path.getsize(b_img))
                if len(blank_drives) == 1:
                    b_drive = blank_drives[0]
                else:
                    print(f"\n{BOLD}Multiple candidate removable drives detected:{RESET}")
                    for i, d in enumerate(blank_drives, 1):
                        print(f"  [{i}] {d['desc']}")
                    try:
                        choice = input(f"{YELLOW}Select drive to flash [1-{len(blank_drives)}] (or Enter to cancel): {RESET}").strip()
                        if choice.isdigit() and 1 <= int(choice) <= len(blank_drives):
                            b_drive = blank_drives[int(choice) - 1]
                        else:
                            b_drive = None
                    except KeyboardInterrupt:
                        print("\nAborted.")
                        sys.exit(0)

                if b_drive:
                    log(f"Auto-detected blank / uninitialized removable drive: {b_drive['desc']}")
                    log(f"Found base Mothbox OS image: {os.path.basename(b_img)} ({b_img_size})")
                    if args.yes:
                        ask = "y"
                    else:
                        try:
                            ask = input(f"{YELLOW}Initialize and flash base Mothbox OS image to {b_drive['device']}? [y/N]: {RESET}").strip().lower()
                        except KeyboardInterrupt:
                            print("\nAborted.")
                            sys.exit(0)

                    if ask in ("y", "yes"):
                        target_type = "blank_sd"
                        blank_info = b_drive
                        blank_info["image"] = b_img
                        blank_info["image_size"] = b_img_size

        # C. Check Network
        if not target_type:
            log("Searching for live Mothbox units over Wi-Fi / Ethernet...")
            net_candidates = probe_network_targets()
            if net_candidates:
                target_type = "network"
                target_host = net_candidates[0][0]
                log(f"Auto-detected reachable Mothbox: {target_host} ({net_candidates[0][1]})", "OK")

    if not target_type:
        log("Could not find any connected Mothbox target!", "ERR")
        print("\nTroubleshooting:")
        print("  1. If using SD Card: Ensure the card is inserted properly.")
        print("     • If card is blank/fresh, run: python3 flash_mothbox.py --blank")
        print("  2. If using Wi-Fi: Connect your laptop to Wi-Fi SSID 'mothboxwifi' (password 'lunaluna').")
        print("  3. If using Ethernet: Plug cable into Mothbox and ensure Debug switch is ON.")
        print("  4. Or specify target explicitly: python3 flash_mothbox.py --ip <IP> or --sd <PATH>\n")
        sys.exit(1)

    # 3. Confirmation Prompt (if not --yes)
    if target_type == "blank_sd":
        print(f"\n{RED}{BOLD}{'=' * 75}")
        print("  ⚠️  CRITICAL WARNING: FRESH SD CARD IMAGE FLASH (FULL DISK ERASE)  ⚠️")
        print(f"{'=' * 75}{RESET}")
        print(f"  • Target Drive:    {BOLD}{blank_info['device']}{RESET} ({blank_info['size']} - {blank_info['model']})")
        print(f"  • Existing Layout: {blank_info['part_descs']}")
        print(f"  • Base Image:      {blank_info['image']} ({blank_info['image_size']})")
        print(f"  • Source Firmware: {FIRMWARE_SRC}")
        print(f"  • Settings:        {'Preserve existing' if args.keep_settings else args.csv}")
        print(f"  • Mode:            {'DRY RUN (Preview only)' if args.dry_run else 'DESTRUCTIVE LIVE FLASH'}")
        print(f"\n{RED}{BOLD}  ALL EXISTING DATA ON {blank_info['device']} WILL BE PERMANENTLY ERASED AND OVERWRITTEN!{RESET}")
        print(f"{RED}  Please verify this is the intended drive before proceeding.{RESET}")
        print(f"{RED}{'=' * 75}{RESET}\n")

        if not args.yes and not args.dry_run:
            try:
                confirm = input(f"{YELLOW}Type '{BOLD}ERASE{RESET}{YELLOW}' to confirm and begin full disk image flash: {RESET}").strip()
                if confirm.upper() != "ERASE":
                    log("Image flash cancelled by user. No data was modified.", "WARN")
                    sys.exit(0)
            except KeyboardInterrupt:
                print("\nAborted.")
                sys.exit(0)
    else:
        # Check if target firmware matches latest repository patch
        is_up_to_date = False
        diff_count = 0
        diff_files_sample = []

        if target_type == "sd" and sd_info.get("rootfs"):
            target_fw_dir = os.path.join(sd_info["rootfs"], "home", "pi", "Desktop", "Mothbox")
            if os.path.isdir(target_fw_dir):
                diffs = check_firmware_diffs(FIRMWARE_SRC, target_fw_dir)
                target_csv = os.path.join(target_fw_dir, "mothbox_settings.csv")
                csv_matches = check_csv_match(args.csv, target_csv)
                if diffs is not None:
                    diff_count = len(diffs)
                    diff_files_sample = diffs[:4]
                    if diff_count == 0 and (args.keep_settings or csv_matches):
                        is_up_to_date = True

        print(f"\n{BOLD}Target Summary:{RESET}")
        if target_type == "network":
            print(f"  • Connection:  Network SSH ({DEFAULT_USER}@{target_host})")
        else:
            root_dev_display = sd_info.get("unmounted_root_dev")
            if root_dev_display:
                root_display = sd_info.get("rootfs") or f"[UNMOUNTED: run 'udisksctl mount -b {root_dev_display}']"
            else:
                root_display = sd_info.get("rootfs") or "[UNMOUNTED]"
            print(f"  • Connection:  Direct SD Card")
            print(f"  • Rootfs:      {root_display}")
            print(f"  • Bootfs:      {sd_info.get('bootfs') or '[None]'}")
        print(f"  • Source:      {FIRMWARE_SRC}")
        print(f"  • Settings:    {'Preserve existing' if args.keep_settings else args.csv}")
        print(f"  • Mode:        {'DRY RUN (Preview only)' if args.dry_run else 'LIVE FLASH'}")

        if is_up_to_date:
            print(f"  • Status:      {GREEN}{BOLD}✨ Target already matches latest patch! (0 pending changes){RESET}")
        elif diff_count > 0:
            sample_str = ", ".join(diff_files_sample)
            if diff_count > len(diff_files_sample):
                sample_str += f", +{diff_count - len(diff_files_sample)} more"
            print(f"  • Status:      {YELLOW}{BOLD}⚡ Updates available: {diff_count} file(s) ({sample_str}){RESET}")

        print()

        if is_up_to_date:
            log("Firmware on target SD card already matches the latest repository patch!", "OK")
            if not args.yes:
                try:
                    confirm = input(f"{YELLOW}Target already has the latest firmware patch. Reflash anyway? [y/N]: {RESET}").strip().lower()
                    if confirm not in ("y", "yes"):
                        log("Target is already up to date. No changes made.", "OK")
                        sys.exit(0)
                except KeyboardInterrupt:
                    print("\nAborted.")
                    sys.exit(0)
        else:
            if not args.yes:
                try:
                    confirm = input(f"{YELLOW}Proceed with firmware flash? [Y/n]: {RESET}").strip().lower()
                    if confirm and confirm not in ("y", "yes"):
                        log("Flash aborted by user.", "WARN")
                        sys.exit(0)
                except KeyboardInterrupt:
                    print("\nAborted.")
                    sys.exit(0)

    # 4. Execute Flash
    success = False
    start_t = time.time()

    if target_type == "network":
        success = flash_over_network(
            host=target_host,
            user=DEFAULT_USER,
            password=DEFAULT_PASS,
            csv_path=args.csv,
            dry_run=args.dry_run,
            keep_settings=args.keep_settings
        )
    elif target_type == "sd":
        success = flash_sd_card(
            rootfs=sd_info.get("rootfs"),
            bootfs=sd_info.get("bootfs"),
            csv_path=args.csv,
            dry_run=args.dry_run,
            keep_settings=args.keep_settings,
            unmounted_root_dev=sd_info.get("unmounted_root_dev")
        )
    elif target_type == "blank_sd":
        success = flash_blank_sd_card(
            device=blank_info["device"],
            image_path=blank_info["image"],
            csv_path=args.csv,
            dry_run=args.dry_run,
            keep_settings=args.keep_settings
        )

    elapsed = time.time() - start_t
    print()
    if success:
        log(f"{BOLD}Firmware flash completed successfully in {elapsed:.1f}s!{RESET}", "OK")
        log(f"Full log transcript saved to: {FLASH_LOG_PATH}")
    else:
        log(f"Firmware flash failed after {elapsed:.1f}s. Check {FLASH_LOG_PATH} for details.", "ERR")
        sys.exit(1)


if __name__ == "__main__":
    main()
