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
        "grep -a 'Boot time :' /home/pi/Desktop/Mothbox/logs/Scheduler_log.txt",
        "journalctl --list-boots",
        "ls -la /home/pi/Desktop/Photos/",
        "tail -n 30 /home/pi/Desktop/Mothbox/logs/TakePhoto_log.txt"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
