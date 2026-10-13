import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=15)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

child.sendline("""python3 -c '
import fcntl, sys
try:
    f = open("/run/mothbox_takephoto.lock", "w")
    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print("flock SUCCESS")
except Exception as e:
    print(f"flock FAILED: {type(e).__name__}: {e}")
'
""")
child.expect(r"\$")
print(child.before.decode())
child.sendline("exit")
