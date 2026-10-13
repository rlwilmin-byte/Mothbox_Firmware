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
        "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/logs/Scheduler_log.txt\", \"rb\") as f: c = f.read().decode(\"latin1\", \"ignore\"); idx = c.rfind(\"2026-10-12 07:28\"); print(c[idx:idx+2500]) if idx != -1 else print(\"Not found\")'",
        "cat /boot/firmware/mothbox_custom/system/controls/switches.txt 2>/dev/null"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
