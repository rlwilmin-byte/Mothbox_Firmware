import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "sudo vcgencmd get_throttled",
    "sudo dmesg -T | grep -iE 'undervoltage|throttled|brownout|voltage' | tail -n 20",
    "cat /boot/firmware/config.txt | grep -iE 'current|psu|voltage|boost'"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
