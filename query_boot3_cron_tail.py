import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "sudo journalctl -b -3 -u cron.service --no-pager | tail -n 40",
    "ls -lat /home/pi/Desktop/Mothbox/photos/ | head -n 10",
    "cat /boot/firmware/mothbox_custom/system/controls/switches.txt"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
