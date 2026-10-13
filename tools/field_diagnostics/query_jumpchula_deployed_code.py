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
        "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/TakePhoto.py\") as f: c = f.read(); print(\"BOOT_LOCK in TakePhoto:\", \"BOOT_LOCK\" in c, \"180\" in c)'",
        "python3 -c 'with open(\"/home/pi/Desktop/Mothbox/Scheduler.py\") as f: c = f.read(); print(\"epaper_mode in Scheduler:\", \"epaper_mode\" in c, \"last_shutdown_epoch\" in c)'"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
