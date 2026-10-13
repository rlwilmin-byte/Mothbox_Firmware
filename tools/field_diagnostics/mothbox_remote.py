#!/usr/bin/env python3
"""
Mothbox Remote Diagnostic & Field Query CLI
Consolidated tool for inspecting, querying, and managing remote Mothbox units over SSH.

Usage:
    python3 mothbox_remote.py status
    python3 mothbox_remote.py journal [--boot 0] [--lines 50] [--unit mothbox]
    python3 mothbox_remote.py power
    python3 mothbox_remote.py cron
    python3 mothbox_remote.py locks
    python3 mothbox_remote.py config
    python3 mothbox_remote.py clock [--sync-rtc] [--disable-fake-hwclock]
    python3 mothbox_remote.py cmd "<arbitrary bash command>"
"""

import argparse
import os
import sys

try:
    import pexpect
except ImportError:
    print("Error: 'pexpect' is required. Install via: pip install pexpect", file=sys.stderr)
    sys.exit(1)


class RemoteSession:
    def __init__(self, host, user, password, timeout=25):
        self.host = host
        self.user = user
        self.password = password
        self.timeout = timeout
        self.child = None

    def connect(self):
        cmd = f"ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 {self.user}@{self.host}"
        self.child = pexpect.spawn(cmd, timeout=self.timeout)
        idx = self.child.expect(["password:", pexpect.TIMEOUT, pexpect.EOF], timeout=15)
        if idx != 0:
            raise ConnectionError(f"Failed to connect to {self.user}@{self.host}: {self.child.before.decode(errors='replace')}")
        self.child.sendline(self.password)
        self.child.expect(r"\$", timeout=15)

    def run(self, cmd, timeout=None):
        if not self.child:
            self.connect()
        t = timeout or self.timeout
        self.child.sendline(cmd)
        self.child.expect(r"\$", timeout=t)
        return self.child.before.decode(errors="replace")

    def close(self):
        if self.child:
            try:
                self.child.sendline("exit")
                self.child.close()
            except Exception:
                pass
            self.child = None


def cmd_status(session, args):
    cmds = [
        ("Uptime & Load", "uptime"),
        ("System Time & RTC", "timedatectl"),
        ("Recent Boots", "journalctl --list-boots | tail -n 5"),
        ("Recent Photos", "ls -ltd /home/pi/Desktop/Mothbox/photos/* | head -n 5"),
        ("Active Locks", "ls -la /run/boot_script_running /run/lock/ /tmp/*.lock 2>/dev/null || true"),
        ("Scheduler Log (Tail)", "tail -n 20 /home/pi/Desktop/Mothbox/logs/Scheduler_log.txt 2>/dev/null || true"),
        ("TakePhoto Log (Tail)", "tail -n 20 /home/pi/Desktop/Mothbox/logs/TakePhoto_log.txt 2>/dev/null || true"),
    ]
    for label, c in cmds:
        print(f"\n===== [ {label} ] =====")
        print(session.run(c))


def cmd_journal(session, args):
    boot_flag = f"-b {args.boot}" if args.boot is not None else ""
    unit_flag = f"-u {args.unit}" if args.unit else ""
    lines_flag = f"-n {args.lines}" if args.lines else ""
    c = f"journalctl {boot_flag} {unit_flag} {lines_flag} --no-pager"
    print(f"\n>> {c}")
    print(session.run(c, timeout=40))


def cmd_power(session, args):
    cmds = [
        ("PMIC Reset / Power Events", "dmesg | grep -iE 'pmic|reset|power|halt' | tail -n 30"),
        ("Journal Power Transitions", "journalctl -b 0 | grep -iE 'shutdown|poweroff|halt|systemd-poweroff' | tail -n 30"),
        ("INA219 / Sensor Rails", "sudo python3 /home/pi/Desktop/Mothbox/scripts/read_INA219.py 2>/dev/null || true"),
    ]
    for label, c in cmds:
        print(f"\n===== [ {label} ] =====")
        print(session.run(c, timeout=30))


def cmd_cron(session, args):
    cmds = [
        ("User Crontab", "crontab -l 2>/dev/null || true"),
        ("System Crontab", "cat /etc/crontab"),
        ("Recent Cron Invocations", "grep -a CRON /var/log/syslog 2>/dev/null | tail -n 30 || journalctl -u cron -n 30 --no-pager"),
    ]
    for label, c in cmds:
        print(f"\n===== [ {label} ] =====")
        print(session.run(c))


def cmd_locks(session, args):
    cmds = [
        ("Boot Lock", "ls -l /run/boot_script_running 2>/dev/null || echo 'No BOOT_LOCK found'"),
        ("Run Lockfiles", "ls -la /run/lock/ /tmp/*.lock 2>/dev/null || true"),
        ("Active Python Processes", "ps aux | grep -E 'python|TakePhoto|Scheduler' | grep -v grep || true"),
    ]
    for label, c in cmds:
        print(f"\n===== [ {label} ] =====")
        print(session.run(c))


def cmd_config(session, args):
    c = "cat /home/pi/Desktop/Mothbox/mothbox_settings.csv 2>/dev/null || cat /home/pi/Desktop/mothbox_settings.csv"
    print("\n===== [ Mothbox Settings CSV ] =====")
    print(session.run(c))


def cmd_clock(session, args):
    if args.disable_fake_hwclock:
        print("\n>> Disabling fake-hwclock...")
        print(session.run("sudo systemctl disable --now fake-hwclock 2>/dev/null || true"))
    if args.sync_rtc:
        print("\n>> Synchronizing hardware clock from system time...")
        print(session.run("sudo hwclock -w"))

    cmds = [
        ("Timedatectl", "timedatectl"),
        ("Hardware Clock", "sudo hwclock -r 2>/dev/null || true"),
        ("Fake Hwclock Service", "systemctl status fake-hwclock --no-pager 2>/dev/null || true"),
    ]
    for label, c in cmds:
        print(f"\n===== [ {label} ] =====")
        print(session.run(c))


def cmd_exec(session, args):
    print(f"\n>> {args.command}")
    print(session.run(args.command, timeout=args.timeout))


def main():
    parser = argparse.ArgumentParser(description="Mothbox Remote Diagnostic CLI")
    parser.add_argument("--host", default=os.getenv("MOTHBOX_HOST", "10.42.0.34"), help="Mothbox IP")
    parser.add_argument("--user", default=os.getenv("MOTHBOX_USER", "pi"), help="SSH user")
    parser.add_argument("--password", default=os.getenv("MOTHBOX_PASS", "luna"), help="SSH password")

    subparsers = parser.add_subparsers(dest="subcommand", help="Diagnostic subcommands")

    subparsers.add_parser("status", help="High-level system status and log tails")

    p_journal = subparsers.add_parser("journal", help="Query systemd journalctl")
    p_journal.add_argument("--boot", "-b", type=str, default="0", help="Boot offset (0=current, -1=prev)")
    p_journal.add_argument("--lines", "-n", type=int, default=50, help="Number of lines")
    p_journal.add_argument("--unit", "-u", type=str, default=None, help="Systemd unit filter")

    subparsers.add_parser("power", help="Inspect PMIC, power rails, and shutdown events")
    subparsers.add_parser("cron", help="Inspect crontab and cron trigger events")
    subparsers.add_parser("locks", help="Inspect runtime locks and running processes")
    subparsers.add_parser("config", help="Read remote mothbox_settings.csv")

    p_clock = subparsers.add_parser("clock", help="Inspect and sync RTC / timezone / fake-hwclock")
    p_clock.add_argument("--sync-rtc", action="store_true", help="Sync RTC from system time (hwclock -w)")
    p_clock.add_argument("--disable-fake-hwclock", action="store_true", help="Disable fake-hwclock service")

    p_exec = subparsers.add_parser("cmd", help="Execute arbitrary command over SSH")
    p_exec.add_argument("command", help="Command to run")
    p_exec.add_argument("--timeout", type=int, default=30, help="Execution timeout in seconds")

    args = parser.parse_args()

    if not args.subcommand:
        parser.print_help()
        sys.exit(1)

    dispatch = {
        "status": cmd_status,
        "journal": cmd_journal,
        "power": cmd_power,
        "cron": cmd_cron,
        "locks": cmd_locks,
        "config": cmd_config,
        "clock": cmd_clock,
        "cmd": cmd_exec,
    }

    session = RemoteSession(args.host, args.user, args.password)
    try:
        session.connect()
    except Exception as e:
        print(f"Connection failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        dispatch[args.subcommand](session, args)
    finally:
        session.close()


if __name__ == "__main__":
    main()
