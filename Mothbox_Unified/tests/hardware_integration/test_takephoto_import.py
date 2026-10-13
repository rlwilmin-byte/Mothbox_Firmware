import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=30)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    """python3 -c "
import sys
sys.path.insert(0, '/home/pi/Desktop/Mothbox')
print('Importing TakePhoto modules...')
from picamera2 import Picamera2
import mothbox_hw
import mothbox_exif
print('Imports successful!')
"
""",
    """python3 -c "
import sys
sys.path.insert(0, '/home/pi/Desktop/Mothbox')
import TakePhoto
"
"""
]

for c in cmds:
    print(f"\n==================== CMD ====================")
    child.sendline(c)
    child.expect(r"\$", timeout=30)
    print(child.before.decode())

child.sendline("exit")
