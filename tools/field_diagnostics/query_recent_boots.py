import pexpect
import sys

def main():
    child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
    child.expect("password:")
    child.sendline("luna")
    child.expect(r"\$")

    cmds = [
        "journalctl -b -4 --no-pager -n 50",
        "journalctl -b -3 --no-pager -n 50",
        "journalctl -b -2 --no-pager -n 50",
        "journalctl -b -1 --no-pager -n 50"
    ]

    for c in cmds:
        print(f"\n==================== CMD: {c} ====================")
        child.sendline(c)
        child.expect(r"\$")
        print(child.before.decode())

    child.sendline("exit")

if __name__ == "__main__":
    main()
