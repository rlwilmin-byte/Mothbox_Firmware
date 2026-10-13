import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=15)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

child.sendline("""sudo python3 -c '
import sys
sys.path.insert(0, "/home/pi/Desktop/Mothbox")
import mothbox_hw, time
print("Testing Flash pulse alone...")
mothbox_hw.rail_12v(True)
time.sleep(0.1)
mothbox_hw.flash_on()
time.sleep(0.2)
mothbox_hw.flash_off()
mothbox_hw.rail_12v(False)
print("FLASH PULSE OK!")
'
""")
child.expect(r"\$")
print(child.before.decode())
child.sendline("exit")
