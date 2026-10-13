import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=15)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

child.sendline("""sudo python3 -c '
import sys
sys.path.insert(0, "/home/pi/Desktop/Mothbox")
import mothbox_hw, time
print("Testing Attractor lights alone...")
mothbox_hw.attract_on()
time.sleep(1)
mothbox_hw.attract_off()
print("ATTRACTOR LIGHTS OK!")
'
""")
child.expect(r"\$")
print(child.before.decode())
child.sendline("exit")
