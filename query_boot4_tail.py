import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "journalctl -b -4 --no-pager | tail -n 35",
    "journalctl -b -4 --no-pager | grep -E 'systemd\\[1\\]: Starting.*Reboot|systemd\\[1\\]: Starting.*Shutdown|sudo.*reboot' | tail -n 10"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
