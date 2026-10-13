import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "cat /sys/devices/platform/rpi_power/power_source 2>/dev/null",
    "sudo /usr/bin/pinctrl get 19,23,27",
    "ls -lt /home/pi/Desktop/Mothbox/photos/* | head -n 5"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
