import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=30)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    """python3 -c "
import sys
sys.path.insert(0, '/home/pi/Desktop/Mothbox')
from picamera2 import Picamera2
import mothbox_hw, time

print('Step 1: Init Picamera2 in full 64MP (9248x6944)...')
cam = Picamera2()
cfg = cam.create_still_configuration(main={'size': (9248, 6944), 'format': 'RGB888'})
cam.configure(cfg)
cam.start()
time.sleep(1)

print('Step 2: Attract on + Flash pulse...')
mothbox_hw.attract_on()
mothbox_hw.flash_on()
time.sleep(0.05)
mothbox_hw.flash_off()
mothbox_hw.attract_off()

print('Step 3: Capturing frame...')
req = cam.capture_request()
pil_img = req.make_image('main')
pil_img.save('/tmp/test_64mp_combined.jpg')
cam.stop()
print('SUCCESS! Combined 64MP test passed!')
"
""",
    "ls -lh /tmp/test_64mp_combined.jpg"
]

for c in cmds:
    print(f"\n==================== CMD ====================")
    child.sendline(c)
    child.expect(r"\$", timeout=30)
    print(child.before.decode())

child.sendline("exit")
