import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "sudo python3 /home/pi/Desktop/Mothbox/Scheduler.py"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    # wait 15 seconds
    idx = child.expect([r"\$", pexpect.TIMEOUT], timeout=15)
    print(child.before.decode())

child.sendline("exit")
