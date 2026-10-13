import pexpect

child = pexpect.spawn("ssh -o StrictHostKeyChecking=no pi@10.42.0.34", timeout=20)
child.expect("password:")
child.sendline("luna")
child.expect(r"\$")

cmds = [
    "sudo sed -i 's/America\/Panama/Australia\/NSW/g' /boot/firmware/mothbox_custom/mothbox_settings.csv",
    "sudo sed -i 's/America\/Panama/Australia\/NSW/g' /boot/firmware/mothbox_custom/system/controls/timezone.txt",
    "python3 /home/pi/Desktop/Mothbox/TimezoneUpdater.py",
    "timedatectl status"
]

for c in cmds:
    print(f"\n==================== CMD: {c} ====================")
    child.sendline(c)
    child.expect(r"\$")
    print(child.before.decode())

child.sendline("exit")
