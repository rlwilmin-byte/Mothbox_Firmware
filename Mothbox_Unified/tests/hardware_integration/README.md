# Hardware Integration Tests

This directory contains integration test runners designed to execute tests against a live Mothbox hardware target over SSH.

## Test Scripts:
- `test_attract.py`: Tests attractor light channels (CH1-CH3, CHEXT).
- `test_rail12v.py`: Tests the 12V switched power rail (`RAIL_12V` / Q8 MOSFET).
- `test_flash.py`: Tests the high-current flash pulse line (`FLASH`).
- `test_flock_open.py`: Validates file locking semantics across reboot transitions.
- `test_camera_noflash.py` / `test_camera_picamera2.py`: Validates OwlSight/Picamera2 sensor pipeline.
- `test_combined_64mp.py` / `test_combined_64mp_path.py`: Integration test for 64MP capture and post-processing.
- `test_subsystems.py`: End-to-end subsystem validation (Diagnostics, TakePhoto, photos directory check).
- `test_sched_pass.py`: Validates scheduler loop pass execution.
- `test_takephoto_import.py`: Checks TakePhoto runtime module imports.
