import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=30)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    # 1. Clean old photos from previous tests to track new captures accurately
    "ls -l /home/pi/Desktop/Mothbox/photos/wittyBee_2026-10-10/ | wc -l",
    # 2. Run Diagnostics check
    "python3 /home/pi/Desktop/Mothbox/Diagnostics.py",
    # 3. Run TakePhoto test
    "python3 /home/pi/Desktop/Mothbox/TakePhoto.py",
    # 4. Verify photo saved
    "ls -lt /home/pi/Desktop/Mothbox/photos/* | head -n 10"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$", timeout=30)
    print(child.before.decode())

child.sendline("exit")
