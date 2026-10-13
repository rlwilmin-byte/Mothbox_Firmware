import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

child.sendline("journalctl -b -2 --no-pager | tail -n 50")
child.expect(r"\$")
print(child.before.decode())

child.sendline("exit")
