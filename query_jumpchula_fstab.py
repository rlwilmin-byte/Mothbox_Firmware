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
        "cat /etc/fstab",
        "sudo crontab -l",
        "ls -la /etc/cron.d/ /etc/cron.daily/",
        "systemctl list-dependencies systemd-journal-flush.service",
        "systemctl list-dependencies multi-user.target | head -n 30"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
