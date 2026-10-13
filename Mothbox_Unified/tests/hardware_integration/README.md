# Live Hardware Integration Test Suite

This directory contains live integration tests executed against a remote Mothbox target over SSH.

## Main Runner: `test_hardware_live.py`
Consolidated runner that executes modular hardware integration tests against the target Mothbox unit.

### Usage
```bash
# Run all tests sequentially
python3 test_hardware_live.py --host 10.42.0.34 --user pi --test all

# Run specific hardware subsystem tests
python3 test_hardware_live.py --test rails       # Switched 12V rail (Q8 MOSFET)
python3 test_hardware_live.py --test attract     # Attractor lights (CH1-CH3, CHEXT)
python3 test_hardware_live.py --test flash       # Flash pulse MOSFET line
python3 test_hardware_live.py --test camera      # Picamera2 initialization
python3 test_hardware_live.py --test 64mp        # OwlSight 64MP pipeline
python3 test_hardware_live.py --test subsystems  # Diagnostics.py & TakePhoto.py
python3 test_hardware_live.py --test scheduler   # Single scheduler pass
python3 test_hardware_live.py --test locks       # flock and BOOT_LOCK semantics
python3 test_hardware_live.py --test imports     # Firmware import integrity
```

Configuration can also be passed via environment variables:
- `MOTHBOX_HOST` (default: `10.42.0.34`)
- `MOTHBOX_USER` (default: `pi`)
- `MOTHBOX_PASS` (default: `luna`)
