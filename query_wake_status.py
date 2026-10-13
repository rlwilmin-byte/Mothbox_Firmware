import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "date; timedatectl",
    "cat /boot/firmware/mothbox_custom/system/controls/switches.txt",
    "cat /sys/class/rtc/rtc0/wakealarm 2>/dev/null",
    "journalctl --list-boots | tail -n 5"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
