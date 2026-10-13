import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/logs/Scheduler_log.txt\", \"rb\") as f: c = f.read().decode(\"latin1\", \"ignore\"); idx = c.rfind(\"2026-10-12 21:54:34\"); print(c[idx+800:idx+3500])'",
    "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/logs/Scheduler_log.txt\", \"rb\") as f: c = f.read().decode(\"latin1\", \"ignore\"); idx = c.rfind(\"2026-10-12 20:31:40\"); print(c[idx+800:idx+3500])'"
]

for c in cmds:
    print(f"\n==================== CMD ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
