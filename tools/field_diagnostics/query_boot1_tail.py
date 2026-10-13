import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "journalctl -b -1 --no-pager | tail -n 50",
    "journalctl -b -1 --no-pager | grep -iE 'shutdown|halt|poweroff|reboot|panic|under-voltage|oom' | tail -n 25"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
