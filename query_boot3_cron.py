import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

child.sendline("sudo journalctl -b -3 -u cron.service --no-pager | head -n 40")
child.expect(r"\$")
print(child.before.decode())

child.sendline("exit")
