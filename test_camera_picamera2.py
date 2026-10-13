import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=30)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    """python3 -c "
from picamera2 import Picamera2
import time
print('Initializing Picamera2...')
cam = Picamera2()
cfg = cam.create_still_configuration(main={'size': (4624, 3472)})
cam.configure(cfg)
cam.start()
time.sleep(1)
print('Taking test still...')
cam.capture_file('/tmp/test_capture.jpg')
cam.stop()
print('SUCCESS! File saved.')
"
""",
    "ls -lh /tmp/test_capture.jpg"
]

for c in cmds:
    print(f"\n==================== CMD ====================")
    child.sendline(c)
    child.expect(r"\$", timeout=30)
    print(child.before.decode())

child.sendline("exit")
