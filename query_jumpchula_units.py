import pexpect
import sys

def main():
    child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
    idx = child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF])
    if idx != 0:
        print("Failed to get password prompt", child.before.decode())
        return
    child.sendline("luna")
    child.expect(r"\$")

    cmds = [
        "cat /etc/rc.local",
        "ls -la /etc/systemd/system/lowpower* /lib/systemd/system/lowpower*",
        "cat /etc/systemd/system/lowpower.timer 2>/dev/null || cat /lib/systemd/system/lowpower.timer 2>/dev/null",
        "cat /etc/systemd/system/lowpower.service 2>/dev/null || cat /lib/systemd/system/lowpower.service 2>/dev/null",
        "dmesg -T | grep -i rtc"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
