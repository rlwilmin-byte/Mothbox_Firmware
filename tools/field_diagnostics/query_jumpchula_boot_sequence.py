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
        "journalctl -b 0 -u systemd-journal-flush.service -n 20 --no-pager",
        "journalctl -b 0 --after-cursor=\"$(journalctl -b 0 -u systemd-journal-flush.service -n 1 --show-cursor | grep cursor | cut -d' ' -f3)\" -n 30 --no-pager 2>/dev/null || journalctl -b 0 -n 100 --no-pager | grep -A 25 'Time spent on flushing'",
        "cat /boot/firmware/cmdline.txt",
        "cat /boot/firmware/config.txt | grep -v '^#' | grep -v '^$'"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
