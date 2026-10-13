import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    """sudo journalctl -b 0 -k --no-pager | grep -i 'pmic'""",
    """sudo journalctl -b 0 -k --no-pager | grep -i 'reset'""",
    """sudo journalctl -b 0 -k --no-pager | grep -i 'bcm2835-wdt'"""
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
