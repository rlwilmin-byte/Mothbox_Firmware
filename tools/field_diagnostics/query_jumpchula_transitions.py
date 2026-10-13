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
        "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/logs/Scheduler_log.txt\", \"rb\") as f: lines = [l.decode(\"latin1\", \"ignore\").strip() for l in f if any(k in l for k in [b\"Boot time :\", b\"Next wakeup alarm\", b\"Shutdown at:\", b\"MODE\"])]; print(\"\\n\".join(lines[-20:]))'",
        "cat /home/pi/Desktop/Mothbox/last_shutdown_epoch.txt 2>/dev/null || echo 'No last_shutdown_epoch.txt'"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
