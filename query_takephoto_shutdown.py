import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/logs/TakePhoto_log.txt\") as f: lines = f.readlines(); print(\"\".join(lines[-35:]))'",
    "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/TakePhoto.py\") as f: c = f.read(); print(\"shutdown in TakePhoto:\", \"shutdown\" in c.lower(), \"poweroff\" in c.lower())'"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
