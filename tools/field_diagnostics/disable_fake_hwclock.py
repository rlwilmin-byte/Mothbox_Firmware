import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "sudo systemctl disable --now fake-hwclock.service 2>/dev/null || true",
    "sudo systemctl mask fake-hwclock.service 2>/dev/null || true",
    "grep fake-hwclock /etc/systemd/system/* 2>/dev/null || echo 'fake-hwclock masked'"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
