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
        "python3 -c 'import datetime; print(\"Wake alarm epoch:\", datetime.datetime.fromtimestamp(1791856800))'",
        "cat /home/pi/Desktop/Mothbox/system/controls/switches.txt 2>/dev/null",
        "ls -la /run/boot_script_running /run/lowpower_running /run/takephoto_running 2>/dev/null || echo 'No run locks'",
        "head -n 25 /boot/firmware/mothbox_custom/system/controls/switches.txt 2>/dev/null"
    ]

    for c in cmds:
        print(f"\n--- CMD: {c} ---")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
