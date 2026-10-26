#!/usr/bin/python

"""
Backupper Script
This script is for folks collecting lots of data automatically that needs to get backed up at certain intervals
for instance saving a bunch of files to a folder, but then automatically copying them to larger external devices
This script first defines paths for the desktop, photos folder, and backup folder name. Then, it defines functions to:

    Get the storage information (total and available space) of a path.
    Find the sizes of all external devices in terms of total storage capacity (not available because this will change)
    Rank them in order  of their total storage capacity
    Check if the first option has space available to copy the new files
      if not, choose the next option in terms of total storage
    Copy all the files from the internal storage to the external storage
    Move the files from the directory of "fresh" files to the internal "backedup" folder
    if the internal storage gets too small, delete the internal "backedup" folder
Finally, the script checks if the photos folder exists and then finds the largest external storage. It compares the total space and available space on both the desktop and the external storage to determine if the external storage has enough space for the backup. If so, it creates a backup folder on the external storage and copies the photos. Otherwise, it informs the user about insufficient space.

Note:
    This script assumes the user running the script has read and write permissions to the desktop and any external storage devices.
    You might need to adjust the user name in desktop_path depending on your Raspberry Pi setup.
"""



import os
import sys
#######---- Check for Boot lock ------
BOOT_LOCK = "/run/boot_script_running"

# --final : the end-of-session backup that Scheduler.py runs deliberately from
# run_shutdown_pi5(), AFTER it has re-armed the boot lock (so no cron-launched
# TakePhoto.py / Backup_Files.py can interfere) and after photo capture has
# stopped. It bypasses both the boot lock and the interval throttle below so
# the last few minutes of photos get copied before power-off. Never pass this
# from cron -- it is only meaningful when the Scheduler is orchestrating the
# shutdown sequence.
FINAL_BACKUP = "--final" in sys.argv[1:]
# (BOOT_LOCK exit guard moved inside __main__ so importing this module does not terminate caller)
#-----------------------------##

import os
import subprocess

import shutil
import psutil
import csv
from pathlib import Path
from datetime import datetime
import sys
import time
import fcntl
import errno

_backup_lock = None

def acquire_backup_lock(final=False):
    """Ensures only one instance of Backup_Files.py runs at a time."""
    global _backup_lock
    for lock_path in ["/run/mothbox_backup.lock", "/tmp/mothbox_backup.lock"]:
        try:
            _backup_lock = open(lock_path, "a+")
            if final:
                # If final shutdown backup, wait up to 15s for in-flight cron backup to finish
                for _ in range(15):
                    try:
                        fcntl.flock(_backup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        return True
                    except (BlockingIOError, OSError):
                        time.sleep(1)
                fcntl.flock(_backup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                fcntl.flock(_backup_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (BlockingIOError, OSError) as e:
            if isinstance(e, BlockingIOError) or getattr(e, "errno", None) in (errno.EAGAIN, errno.EWOULDBLOCK):
                print("[Backup] Another backup is already in progress -- skipping this cycle.")
                sys.exit(0)
            continue
        except Exception as e:
            print(f"[Backup] Could not take backup lock ({e}) -- proceeding anyway.")
            return True
    return True


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

LAST_BACKUP_FILE = CONTROL_ROOT / "last_backup_time.txt"


SETTINGS_CSV = Path("/boot/firmware/mothbox_custom/mothbox_settings.csv")
BACKUP_INTERVAL_DEFAULT = 5
BACKUP_INTERVAL_MIN, BACKUP_INTERVAL_MAX = 1, 999


def get_backup_interval():
    """
    Minutes between backups, read straight from mothbox_settings.csv
    (row: backup_interval,<minutes>,...). Same file the settings editor writes.

    Default 5. Valid range 1-999; anything missing, non-numeric, or out of
    range falls back to the default so a typo can never stall backups.
    """
    if not SETTINGS_CSV.exists():
        return BACKUP_INTERVAL_DEFAULT
    try:
        # utf-8-sig eats the BOM Excel likes to prepend; latin-1 never fails.
        for enc in ("utf-8-sig", "latin-1"):
            try:
                with open(SETTINGS_CSV, newline="", encoding=enc) as f:
                    reader = csv.DictReader(f)
                    if reader.fieldnames:
                        reader.fieldnames = [h.strip().lstrip("\ufeff").upper() for h in reader.fieldnames]
                    if not reader.fieldnames or "SETTING" not in reader.fieldnames or "VALUE" not in reader.fieldnames:
                        return BACKUP_INTERVAL_DEFAULT
                    for row in reader:
                        key = (row.get("SETTING") or "").strip().lstrip("\ufeff")
                        if key == "backup_interval":
                            val = int((row.get("VALUE") or "").strip())
                            if BACKUP_INTERVAL_MIN <= val <= BACKUP_INTERVAL_MAX:
                                return val
                            print(f"Warning: backup_interval={val} outside {BACKUP_INTERVAL_MIN}-{BACKUP_INTERVAL_MAX}, using {BACKUP_INTERVAL_DEFAULT}")
                            return BACKUP_INTERVAL_DEFAULT
                break  # file read fine, key just wasn't there
            except UnicodeDecodeError:
                continue
    except (ValueError, IOError, OSError) as e:
        print(f"Warning: could not read backup_interval from settings ({e}), using {BACKUP_INTERVAL_DEFAULT}")
    return BACKUP_INTERVAL_DEFAULT


def is_backup_due():
    interval_mins = get_backup_interval()
    if not os.path.exists(LAST_BACKUP_FILE):
        return True
    try:
        with open(LAST_BACKUP_FILE) as f:
            last = float(f.read().strip())
        elapsed_mins = (time.time() - last) / 60.0
        threshold = interval_mins - 0.55
        print(f"Backup interval check: {elapsed_mins:.2f} min elapsed, threshold {threshold:.1f} min (interval={interval_mins})")
        return elapsed_mins >= threshold
    except (ValueError, IOError):
        return True


def record_backup_done():
    try:
        tmp = str(LAST_BACKUP_FILE) + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(time.time()))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, LAST_BACKUP_FILE)
    except IOError as e:
        print(f"Warning: could not write last_backup_time: {e}")


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
        print(f"⚠️ Warning: Failed reading {path}: {e}")

    return default


# ---- Load Controls ----

computerName = read_control(CONTROL_ROOT / "name.txt", "name", "errorname")
LastCalibration= float(read_control(CONTROL_ROOT / "lastcalibration.txt", "lastcalibration", 0))

internal_storage_minimum = int(
    read_control(CONTROL_ROOT / "safetygb.txt", "safetygb", 9)
)


# Define paths
desktop_path = Path(
    "/home/pi/Desktop/Mothbox"
)  # Assuming user is "pi" on your Raspberry Pi
photos_folder = desktop_path / "photos"
logs_folder = desktop_path / "logs"
backedup_photos_folder = desktop_path / "photos_backedup"

backup_folder_name = "photos_backup_"+computerName


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

def find_largest_external_storage():
    largest_storage = None
    largest_size = 0

    # CHANGE: Look in /media instead of /media/pi
    for mount_point in os.listdir("/media"):
        path = Path(f"/media/{mount_point}")
        
        # SAFETY: Skip the 'pi' folder itself if it exists, and ignore the SD card
        if mount_point == "pi" or "mmcblk" in mount_point:
            continue

        if is_mounted(path) and path.is_dir():
            total_size, available_size = get_storage_info(path)
            
            # SAFETY: Ignore 0GB ghost drives
            if total_size == 0:
                continue

            if available_size > largest_size:
                largest_storage = path
                largest_size = available_size
                
    return largest_storage
    
    
def is_mounted(path):
  """
  Checks if the given path is currently mounted.
  Args:
      path: The path to check for mount status.

  Returns:
      True if the path is mounted, False otherwise.
  """
  # Use psutil library to check mounted devices
  partitions = psutil.disk_partitions()
  for partition in partitions:
    if partition.mountpoint == str(path):
      return True
  return False

def rsync_photos_to_backup(source_dir, dest_dir):
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    #if you don't want as many slow print commands, turn off verbose mode
    rsync_cmd = ["rsync", "-avz", str(source_dir) + "/", dest_dir]
    # Call rsync using subprocess
    try:
        process = subprocess.run(rsync_cmd, check=True)
    except subprocess.CalledProcessError as err:
        raise RuntimeError(f"Oh no! Mothbox couldn't backup your files!") from err

def rsync_copy_and_delete_files(source_dir, dest_dir):
    """
    This function uses rsync to copy files from source_dir to dest_dir and then deletes the originals from source_dir if successful.
    Args:
      source_dir: The source directory containing the files to copy.
      dest_dir: The destination directory to copy the files to.

    Raises:
      subprocess.CalledProcessError: If the rsync command fails.
    """
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)

    # Build the rsync command with options for recursive copy, delete on source, and verbose output    
    rsync_cmd = ["rsync", "-avz", str(source_dir) + "/", dest_dir]

    # Call rsync using subprocess
    process = subprocess.run(rsync_cmd, check=True)

    # If successful, iterate through copied files and delete them individually
    if process.returncode == 0:
        for root, _, files in os.walk(source_dir):
            for filename in files:
                source_file = os.path.join(root, filename)
                dest_file = os.path.join(dest_dir, filename)
                # Check if the file was successfully copied (exists in destination)
                if os.path.isfile(dest_file):
                    try:
                        os.remove(source_file)
                        #print(f"Deleted: {source_file}")
                    except OSError as e:
                        print(f"Error deleting {source_file}: {e}")

    return process.returncode
def move_folder_contents(source_folder, destination_folder):
  """
  Moves the entire contents of a folder to a new folder, overwriting existing files.
  Args:
      source_folder (str): Path to the source folder.
      destination_folder (str): Path to the destination folder.
  """
  print("moving folder contents")
  for filename in os.listdir(source_folder):
    source_path = os.path.join(source_folder, filename)
    destination_path = os.path.join(destination_folder, filename)

    if os.path.isfile(source_path):
      # Move the file, overwrite if exists
      shutil.move(source_path, destination_path)
    elif os.path.isdir(source_path):
      # Create destination directory if it doesn't exist
      os.makedirs(destination_path, exist_ok=True)
      os.chmod(destination_path, 0o777)
      # Recursively move contents of subfolders
      move_folder_contents(source_path, destination_path)
    else:
      print(f"Skipping unknown item: {filename}")

def move_photos_to_backup(source_folder, target_folder):
  """
  Copies all files and subfolders from the source folder to the target folder recursively,
  handling existing dated folders and copying their contents.
  Args:
      source_folder: The path to the source folder.
      target_folder: The path to the target folder.
  """
  if not os.path.exists(target_folder):
    os.makedirs(target_folder)
  os.chmod(target_folder, 0o777)  # mode=0o777 for read write for all users
  # Move all contents (files and subfolders)
  try:
    shutil.move(source_folder, target_folder)
    print("Contents moved successfully!")
    
    #recreate the empty photos folder
    if not os.path.exists(source_folder):
        os.makedirs(source_folder)
    os.chmod(source_folder, 0o777)
  except OSError as e:
    print("Error moving contents:", e)
  
def copy_photos_to_backup(source_folder, target_folder):
  """
  Copies all files and subfolders from the source folder to the target folder recursively,
  handling existing dated folders and copying their contents.
  Args:
      source_folder: The path to the source folder.
      target_folder: The path to the target folder.
  """
  if not os.path.exists(target_folder):
    os.makedirs(target_folder)
  os.chmod(target_folder, 0o777)  # mode=0o777 for read write for all users

  for item in os.listdir(source_folder):
    source_path = os.path.join(source_folder, item)
    target_path = os.path.join(target_folder, item)

    if os.path.isfile(source_path):
      shutil.copy2(source_path, target_path)  # Copy files
      os.chmod(target_path, 0o777)  # Set permissions for copied files
    else:
      # Handle existing dated folders
      if not os.path.exists(target_path):
        shutil.copytree(source_path, target_path)  # Copy subdirectory if not exists
      else:
        # Copy contents of existing subdirectory
        for inner_item in os.listdir(source_path):
          inner_source_path = os.path.join(source_path, inner_item)
          inner_target_path = os.path.join(target_path, inner_item)
          if os.path.isfile(inner_source_path):
            shutil.copy2(inner_source_path, inner_target_path)
            os.chmod(inner_target_path, 0o777)  # Set permissions for copied files

def copy_folders_with_files(source_folder, target_folder):
    """
    Copies folders from the source folder to the target folder, 
    only if they contain files.

    Args:
        source_folder: The path to the source folder.
        target_folder: The path to the target folder.
    """

    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    for item in os.listdir(source_folder):
        source_path = os.path.join(source_folder, item)
        target_path = os.path.join(target_folder, item)

        if os.path.isfile(source_path):
            # If the source is a file, copy it directly
            try:
                shutil.copy2(source_path, target_path,dirs_exist_ok = True)
                print(f"Copied file: {source_path} to {target_path}")
            except Exception as e:
                print(f"Error copying {source_path} to {target_path}: {e}")
        elif os.path.isdir(source_path):  # Check if it's a directory
            # If it's a directory, check if it contains files
            if not os.listdir(source_path):
                continue  # Skip empty directories
            else:
                # Copy the directory and its contents
                try:
                    shutil.copytree(source_path, target_path,dirs_exist_ok = True)
                    print(f"Copied folder: {source_path} to {target_path}")
                except Exception as e:
                    print(f"Error copying folder {source_path} to {target_path}: {e}")


def verify_copy_bad(source_folder, destination_folder):
    """
    Compares the contents of a source folder and its subdirectories with the destination folder to verify successful copy.
    If an empty directory is found that hasn't been copied, it's ignored.
    If a directory with files is missing, it reports an error.
    Args:
        source_folder: The path to the source folder.
        destination_folder: The path to the destination folder.
    Returns:
        A list of any differences found between the source and destination folders.
    """
    source_path = Path(source_folder)
    dest_path = Path(destination_folder)
    differences = []
    # Check if source folder exists
    if not source_path.exists():
        differences.append(f"Error: Source folder '{source_folder}' does not exist.")
        return differences

    # Compare files and subdirectories recursively
    for root, dirs, files in os.walk(source_path):
        rel_path = os.path.relpath(root, source_path)
        dest_dir = os.path.join(dest_path, rel_path)

        # Check if corresponding directory exists in destination
        if not os.path.exists(dest_dir):
            # If directory doesn't exist, check if it's empty
            if not os.listdir(dest_dir):
                # If it's empty, ignore it
                continue
            else:
                differences.append(f"Missing directory in destination: {dest_dir}")
                continue

        # Compare files within the directory
        for filename in files:
            source_file = os.path.join(root, filename)
            dest_file = os.path.join(dest_dir, filename)

            # Check if file exists in destination
            if not os.path.isfile(dest_file):
                differences.append(f"Missing file in destination: {dest_file}")

    return differences

def verify_copy(source_folder, destination_folder):
  """
  Compares the contents of a source folder and its subdirectories with the destination folder to verify successful copy.
  Args:
      source_folder: The path to the source folder.
      destination_folder: The path to the destination folder.
  Returns:
      A list of any differences found between the source and destination folders.
  """
  source_path = Path(source_folder)
  dest_path = Path(destination_folder)
  differences = []

  # Check if source folder exists
  if not source_path.exists():
    differences.append(f"Error: Source folder '{source_folder}' does not exist.")
    return differences

  # Compare files and subdirectories recursively
  for root, dirs, files in os.walk(source_path):
    rel_path = os.path.relpath(root, source_path)
    dest_dir = os.path.join(dest_path, rel_path)

    # Check if corresponding directory exists in destination
    if not os.path.exists(dest_dir):
      differences.append(f"Missing directory in destination: {dest_dir}")
      continue

    # Compare files within the directory
    for filename in files:
      source_file = os.path.join(root, filename)
      dest_file = os.path.join(dest_dir, filename)

      # Check if file exists in destination
      if not os.path.isfile(dest_file):
        differences.append(f"Missing file in destination: {dest_file}")
  return differences

def delete_folder_contents(folder_path):
  """
  Deletes all contents (files and subdirectories) from a folder.
  Args:
      folder_path: The path to the folder to be emptied.
  """
  for root, dirs, files in os.walk(folder_path, topdown=False):
    for filename in files:
      file_path = os.path.join(root, filename)
      os.remove(file_path)
    for dir in dirs:
      dir_path = os.path.join(root, dir)
      os.rmdir(dir_path)

def delete_original_photos(source_folder):
    """
    Deletes all files from the source folder.
    Args:
        source_folder: The path to the source folder.
    """
    print("trying to delete fresh")
    for filename in os.listdir(source_folder):
        file_path = os.path.join(source_folder, filename)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError as e:
            print(f"Error deleting file {file_path}: {e}")
            
def get_dir_size(dir_path):
  """
  Calculates the total size of a directory and its subdirectories.
  Args:
      dir_path: The path to the directory.
  Returns:
      The total size of the directory in bytes.
  """
  total_size = 0
  for dirpath, dirnames, filenames in os.walk(dir_path):
    for filename in filenames:
      file_path = os.path.join(dirpath, filename)
      if not os.path.islink(file_path):  # Skip symbolic links (optional)
        total_size += os.path.getsize(file_path)
  return total_size

def snapshot_files(folder):
    """Return the set of all file paths currently in folder (recursive)."""
    result = set()
    for root, dirs, files in os.walk(str(folder)):
        for f in files:
            result.add(os.path.join(root, f))
    return result


def move_snapshot_files(file_set, source_root, dest_root):
    """
    Move only the files in file_set from source_root to dest_root.
    Files that appeared in source_root after the snapshot are left untouched,
    keeping them in photos/ so the next backup cycle picks them up.
    """
    if not os.path.exists(dest_root):
        os.makedirs(dest_root)
        os.chmod(dest_root, 0o777)
    for source_file in sorted(file_set):
        if not os.path.exists(source_file):
            continue
        rel = os.path.relpath(source_file, str(source_root))
        dest_file = os.path.join(str(dest_root), rel)
        os.makedirs(os.path.dirname(dest_file), exist_ok=True)
        shutil.move(source_file, dest_file)


def remove_empty_dirs(folder):
    """Remove empty subdirectories bottom-up; leaves the folder itself intact."""
    for root, dirs, files in os.walk(str(folder), topdown=False):
        for d in dirs:
            dirpath = os.path.join(root, d)
            try:
                os.rmdir(dirpath)  # only succeeds if the directory is empty
            except OSError:
                pass


def purge_oldest_backedup_until_threshold(folder, target_free_bytes):
    """
    Delete oldest dated subdirs or files from photos_backedup/ one at a time until
    SD card free space reaches target_free_bytes.
    Only call this when external storage is confirmed connected.
    Never touches photos/ (fresh, unbackedup data).
    """
    if not os.path.exists(folder):
        return
    items = sorted(Path(folder).iterdir(), key=lambda p: p.stat().st_mtime)
    for item in items:
        _, current_free = get_storage_info(desktop_path)
        if current_free >= target_free_bytes:
            break
        if item.is_dir():
            dir_size = get_dir_size(item)
            shutil.rmtree(item)
            print(f"Purged oldest backed-up folder: {item} ({dir_size/1e9:.2f} GB freed)")
        elif item.is_file():
            file_size = item.stat().st_size
            item.unlink()
            print(f"Purged oldest backed-up file: {item.name} ({file_size/1e6:.1f} MB freed)")


MAX_BATCH_BYTES = int(1.2 * 1024**3)  # ~1.2 GB per backup run (safely transfers in ~15-25s)
SAFETY_MARGIN_BYTES = 50 * 1024 * 1024  # 50 MB safety cushion on external drive


def reclaim_orphaned_rec_files(disk_path, min_free_bytes=5 * 1024**3):
    """
    Scans for FSCK*.REC or *.rec files in the root of the external drive.
    If available space is less than min_free_bytes, removes orphaned .REC
    files to restore usable drive capacity for live backups.
    If space is ample, moves them to a quarantine directory '/.fsck_salvage/'.
    """
    disk_path = Path(disk_path)
    rec_set = set(disk_path.glob("FSCK*.REC")).union(disk_path.glob("fsck*.rec")).union(disk_path.glob("*.REC"))
    rec_files = sorted(rec_set)
    if not rec_files:

        return 0

    total_rec_bytes = sum(f.stat().st_size for f in rec_files if f.is_file())
    _, avail_bytes = get_storage_info(disk_path)
    print(f"[Backup] Found {len(rec_files)} orphaned .REC files ({total_rec_bytes / 1e9:.2f} GB) on {disk_path}")

    reclaimed_bytes = 0
    if avail_bytes < min_free_bytes or avail_bytes < total_rec_bytes:
        print(f"[Backup] External free space ({avail_bytes / 1e9:.2f} GB) is constrained. Purging orphaned .REC files to restore capacity...")
        for rf in rec_files:
            try:
                sz = rf.stat().st_size
                rf.unlink()
                reclaimed_bytes += sz
            except Exception as e:
                print(f"[Backup] Error deleting {rf}: {e}")
        print(f"[Backup] Successfully reclaimed {reclaimed_bytes / 1e9:.2f} GB from orphaned .REC files.")
    else:
        salvage_dir = disk_path / ".fsck_salvage"
        salvage_dir.mkdir(exist_ok=True)
        for rf in rec_files:
            try:
                shutil.move(str(rf), str(salvage_dir / rf.name))
            except Exception:
                pass
        print(f"[Backup] Quarantined {len(rec_files)} .REC files into {salvage_dir}")

    return reclaimed_bytes


def incremental_backup_photos(source_dir, dest_root, backedup_dir, max_bytes=MAX_BATCH_BYTES):
    """
    Transfers files iteratively up to max_bytes.
    As each individual photo is copied and verified, it is immediately moved
    to backedup_dir.
    If ENOSPC or out of space occurs, aborts immediately without hanging.
    Returns (num_copied, total_bytes_copied, disk_full_flag).
    """
    source_path = Path(source_dir)
    dest_path = Path(dest_root)
    backedup_path = Path(backedup_dir)

    dest_path.mkdir(parents=True, exist_ok=True)
    backedup_path.mkdir(parents=True, exist_ok=True)

    all_files = []
    for root, _, files in os.walk(str(source_path)):
        for f in sorted(files):
            fp = Path(root) / f
            if fp.is_file():
                all_files.append(fp)

    if not all_files:
        print("[Backup] No photos in photos/ pending backup.")
        return 0, 0, False

    total_needed_all = sum(f.stat().st_size for f in all_files)
    print(f"[Backup] Total photos pending backup: {len(all_files)} files ({total_needed_all / 1e9:.2f} GB)")

    copied_count = 0
    copied_bytes = 0
    disk_full = False

    for src_file in all_files:
        if copied_bytes >= max_bytes and not FINAL_BACKUP:
            print(f"[Backup] Reached batch ceiling ({copied_bytes / 1e6:.1f} MB >= {max_bytes / 1e6:.1f} MB). Remaining files will transfer on next cycle.")
            break

        file_sz = src_file.stat().st_size

        # Check destination free space
        _, avail_bytes = get_storage_info(dest_path)
        if avail_bytes < (file_sz + SAFETY_MARGIN_BYTES):
            print(f"[Backup] Insufficient external space on {dest_path} for {src_file.name} "
                  f"(need {file_sz / 1e6:.1f} MB + margin, available {avail_bytes / 1e6:.1f} MB). Stopping batch.")
            disk_full = True
            break

        rel = src_file.relative_to(source_path)
        target_file = dest_path / rel
        target_file.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(str(src_file), str(target_file))
        except OSError as e:
            if getattr(e, 'errno', None) == 28:  # ENOSPC
                print(f"[Backup] ENOSPC (disk full) during copy of {src_file.name}. Removing partial file.")
                if target_file.exists():
                    try:
                        target_file.unlink()
                    except Exception:
                        pass
                disk_full = True
                break
            else:
                print(f"[Backup] Error copying {src_file.name}: {e}")
                continue

        if target_file.exists() and target_file.stat().st_size == file_sz:
            backup_target = backedup_path / rel
            backup_target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src_file), str(backup_target))
                copied_count += 1
                copied_bytes += file_sz
            except FileNotFoundError:
                # Concurrent backup or external move already relocated the file
                pass
            except Exception as e:
                print(f"[Backup] Warning: could not move {src_file.name} to backedup: {e}")
        else:
            print(f"[Backup] Verification failed for {src_file.name} -- leaving in source directory.")

    remove_empty_dirs(source_path)
    print(f"[Backup] Batch complete: {copied_count} photos moved to backup ({copied_bytes / 1e6:.1f} MB transferred).")
    return copied_count, copied_bytes, disk_full



def backup_and_delete(source_folder, destination_folder):
  """
  Back up files and delete them
  Args:
      source_folder: path to the original location
      destination_folder: path to backup location
  """
  # Ensure source and destination folders exist
  if not os.path.exists(source_folder):
      print(f"Source folder '{source_folder}' does not exist.")
      return
  if not os.path.exists(destination_folder):
      os.makedirs(destination_folder)
      print(f"Created destination folder '{destination_folder}'.")
  try:
      # Copy the contents of the source folder to the destination folder
      for item in os.listdir(source_folder):
          src_path = os.path.join(source_folder, item)
          dest_path = os.path.join(destination_folder, item)
          if os.path.isdir(src_path):
              shutil.copytree(src_path, dest_path)
          else:
              shutil.copy2(src_path, dest_path)
      print(f"All contents of '{source_folder}' successfully copied to '{destination_folder}'.")
      # Verify the copy
      src_items = set(os.listdir(source_folder))
      dest_items = set(os.listdir(destination_folder))
      if not src_items.issubset(dest_items):
          print("Error: Not all items were copied successfully.")
          return
      # Delete the contents of the source folder
      for item in os.listdir(source_folder):
          src_path = os.path.join(source_folder, item)
          if os.path.isdir(src_path):
              shutil.rmtree(src_path)
          else:
              os.remove(src_path)
      print(f"All contents of '{source_folder}' have been deleted.")
  except Exception as e:
      print(f"An error occurred: {e}")



def is_takephoto_active():
    """Returns True if TakePhoto.py is currently running, indicating in-flight capture."""
    try:
        res = subprocess.run(
            ["pgrep", "-f", "Mothbox/TakePhoto.py"],
            capture_output=True, text=True, check=False
        )
        return res.returncode == 0
    except Exception:
        return False

if __name__ == "__main__":
    FINAL_BACKUP = "--final" in sys.argv[1:]
    if os.path.exists(BOOT_LOCK) and not FINAL_BACKUP:
        sys.exit(0)

    if not FINAL_BACKUP and is_takephoto_active():
        print("[Backup] TakePhoto is actively capturing/bursting -- deferring backup to prevent compound inrush power sag.")
        sys.exit(0)

    acquire_backup_lock(final=FINAL_BACKUP)

    if FINAL_BACKUP:
        print("Final end-of-session backup requested -- skipping interval throttle.")
    elif not is_backup_due():
        print(f"Backup not due yet (interval={get_backup_interval()} min). Skipping.")
        sys.exit(0)

    print("----------------- STARTING BACKUP FILES-------------------")
    now = datetime.now()
    formatted_time = now.strftime("%Y-%m-%d %H:%M:%S")
    print(f"Current time: {formatted_time}")

    # Check if "photos" folder exists
    if not os.path.exists(photos_folder):
        print("Photos folder not found, exiting.")
        exit(1)


    # Get total and available space on desktop
    desktop_total, desktop_available = get_storage_info(desktop_path)
    print("Desktop Total    Storage: \t" + str(desktop_total/1000000000))
    print("Desktop Available Storage: \t" + str(desktop_available/1000000000))

    # Find all external drives and rank by available space (descending)
    disks = {}
    for mount_point in os.listdir("/media"):
        path = Path(f"/media/{mount_point}")
        if mount_point == "pi" or "mmcblk" in mount_point:
            continue
        if path.is_dir() and is_mounted(path):
            total_size, available_size = get_storage_info(path)
            if total_size > 0:
                disks[path] = total_size, available_size

    print("~~~sorting disks~~~~~~")
    if disks:
        sorted_disks = sorted(disks.items(), key=lambda item: item[1][0], reverse=True)
        print("External Drives (Ranked by Total Size - Descending):")
        for disk_name, capacity in sorted_disks:
            print(
                f"{disk_name}: total size {capacity[0]/1000000000} GB - available size {capacity[1]/1000000000} GB"
            )
    else:
        print("No external drives found.")
        print("Skipping backup. Not purging photos_backedup because no external storage is available to confirm data is safe.")
        exit(1)
    print("~~~sorted~~~~~~")

    # Check if internal storage is running low.
    # Only purge backed-up photos if external storage is confirmed present (already checked above).
    # Never purge photos/ (unbackedup fresh data).
    x = internal_storage_minimum
    print("Internal storage threshold: " + str(x * 1024**3/1000000000))
    if desktop_available < x * 1024**3:
        print("Low internal storage — purging oldest backed-up photos (external storage confirmed present).")
        purge_oldest_backedup_until_threshold(backedup_photos_folder, x * 1024**3)
        # Refresh available space after purge
        _, desktop_available = get_storage_info(desktop_path)
        print(f"Internal storage after purge: {desktop_available/1000000000:.2f} GB available")
    else:
        print("More than " + str(x) + "GB remain so original backed up files are kept")

    thingsworkedok = False
    for disk_name, capacity in sorted_disks:
        print("chosen Disk: " + str(disk_name))
        
        # 1. Audit and reclaim orphaned .REC files if space is constrained
        reclaim_orphaned_rec_files(disk_name, min_free_bytes=5 * 1024**3)
        
        # 2. Refresh capacity
        total_available, external_available = get_storage_info(disk_name)
        print("total available \t" + str(total_available/1000000000))
        print("external available \t" + str(external_available/1000000000))
        
        # 3. Check minimum buffer
        if external_available < SAFETY_MARGIN_BYTES:
            print(f"External storage {disk_name} has insufficient free space (<50MB). Trying next disk.")
            continue

        external_backup_folder = disk_name / backup_folder_name
        
        # 4. Perform incremental backup
        copied_cnt, copied_b, disk_full = incremental_backup_photos(
            photos_folder, external_backup_folder, backedup_photos_folder
        )
        
        # 5. Back up logs
        logfolder = "logs_" + computerName
        external_logs_folder = disk_name / logfolder
        try:
            copy_photos_to_backup(logs_folder, external_logs_folder)
            print(f"Logs successfully copied to external backup folder: {external_logs_folder}")
        except Exception as e:
            print(f"Warning: could not copy logs to {external_logs_folder}: {e}")

        # If at least some progress was made or no photos were pending, mark success
        if not disk_full or copied_cnt > 0:
            thingsworkedok = True
            break

    if thingsworkedok == False:
        print("stuff never worked out with this backup, your files are not properly backedup")
        record_backup_done()  # Record attempt to enforce interval backoff (avoid 60s thrashing)
        sys.exit(2)
    else:
        record_backup_done()
        print("stuff worked out BACKUP COMPLETE")
    print("end")



