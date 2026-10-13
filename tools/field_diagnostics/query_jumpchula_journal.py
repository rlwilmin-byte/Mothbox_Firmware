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
        "ls -la /var/log/journal/873d2d43460b4b2f9094569b40bb5f52/",
        "find /home/pi/Desktop/Mothbox/photos/ -type f | wc -l",
        "find /media/ -maxdepth 3 2>/dev/null",
        "cat /home/pi/Desktop/Mothbox/logs/Scheduler_log.txt | grep -E 'Boot time :|Next wakeup alarm set for:|Shutdown at:' | tail -n 25"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
