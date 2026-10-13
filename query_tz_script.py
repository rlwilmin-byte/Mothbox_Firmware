import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "cat /home/pi/Desktop/Mothbox/scripts/Set_Timezones.py",
    "cat /home/pi/Desktop/Mothbox/scripts/TimezoneUpdater.py 2>/dev/null || cat /home/pi/Desktop/Mothbox/TimezoneUpdater.py 2>/dev/null"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
