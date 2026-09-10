#!/usr/bin/env python3
import argparse
import datetime as dt
import fcntl
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

SITE_RE = re.compile(r"^SJC-NVIDIA-3200-50(0[1-9]|1[0-6])$")

def expected_eno1(site):
    m = SITE_RE.fullmatch(site)
    if not m:
        raise ValueError("Invalid site: expected SJC-NVIDIA-3200-5001..5016")
    return f"192.168.0.{100 + int(m.group(1))}"

def run(cmd):
    p = subprocess.run(cmd, text=True, stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError(p.stderr.strip() or "command failed")
    return p.stdout.strip()

def iface_exists(name):
    return Path(f"/sys/class/net/{name}").exists()

def ipv4(name):
    if not iface_exists(name):
        return []
    data = json.loads(run(["ip", "-j", "-4", "addr", "show", "dev", name]))
    result = []
    for x in data[0].get("addr_info", []) if data else []:
        if x.get("family") == "inet":
            result.append({
                "address": x.get("local"),
                "prefixlen": x.get("prefixlen")
            })
    return result

def mac(name):
    p = Path(f"/sys/class/net/{name}/address")
    return p.read_text().strip() if p.exists() else ""

def state(name):
    p = Path(f"/sys/class/net/{name}/operstate")
    return p.read_text().strip() if p.exists() else "absent"

def mode(name):
    if not iface_exists(name):
        return "ABSENT"
    try:
        method = run(["nmcli", "-g", "IP4.METHOD", "device", "show", name])
        if method == "auto":
            return "DHCP"
        if method == "manual":
            return "STATIC"
    except Exception:
        pass

    lease_dir = Path("/run/systemd/netif/leases")
    if lease_dir.exists():
        for p in lease_dir.iterdir():
            try:
                text = p.read_text(errors="ignore")
                if re.search(rf"(?m)^INTERFACE={re.escape(name)}$", text):
                    return "DHCP"
            except OSError:
                pass
    return "UNKNOWN"

def in_172(ip):
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network("172.0.0.0/8")
    except ValueError:
        return False

def collect_iface(name, expected=None, dynamic_172=False):
    ips = ipv4(name)
    values = [x["address"] for x in ips]
    return {
        "present": iface_exists(name),
        "adapter": name,
        "mac": mac(name),
        "link_state": state(name),
        "ip_mode": mode(name),
        "ipv4": ips,
        "expected_ip": expected or "",
        "expected_ip_present": expected in values if expected else None,
        "dynamic_172_ip_present": (
            any(in_172(x) for x in values) if dynamic_172 else None
        )
    }

def collect(site, eno1_ip):
    return {
        "site": site,
        "hostname": socket.gethostname(),
        "eno1": collect_iface("eno1", eno1_ip),
        # enp150 has no fixed IP; its actual dynamic address is captured.
        "enp150": collect_iface("enp150", dynamic_172=True)
    }

def stamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def load(path):
    if not path.exists():
        return {"version": 1, "hosts": {}}
    with path.open() as f:
        d = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("hosts"), dict):
        raise RuntimeError("Invalid baseline JSON")
    return d

def lock(path):
    p = Path(str(path) + ".lock")
    p.parent.mkdir(parents=True, exist_ok=True)
    f = p.open("a+")
    fcntl.flock(f, fcntl.LOCK_EX)
    return f

def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".baseline.")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)

def comparable(x):
    return {
        "site": x["site"],
        "hostname": x["hostname"],
        "eno1": x["eno1"],
        "enp150": x["enp150"]
    }

def save(args):
    ip = args.ip or expected_eno1(args.site)
    current = collect(args.site, ip)
    path = Path(args.baseline_file)
    f = lock(path)
    try:
        db = load(path)
        old = db["hosts"].get(args.site)
        current["baseline_created_utc"] = (
            old.get("baseline_created_utc", stamp()) if old else stamp()
        )
        current["baseline_updated_utc"] = stamp()
        db["hosts"][args.site] = current
        write_atomic(path, db)
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()
    print(f"BASELINE_SAVED site={args.site} eno1={ip}")
    return 0

def verify(args):
    ip = args.ip or expected_eno1(args.site)
    current = collect(args.site, ip)
    path = Path(args.baseline_file)
    f = lock(path)
    try:
        db = load(path)
        old = db["hosts"].get(args.site)
        if old is None:
            print(f"ERROR: no pre-reboot baseline for {args.site}",
                  file=sys.stderr)
            return 20

        if comparable(old) == comparable(current):
            old["last_verified_utc"] = stamp()
            db["hosts"][args.site] = old
            write_atomic(path, db)
            print(f"NO_CHANGE site={args.site}")
            return 0

        print(f"CHANGE_DETECTED site={args.site}")
        print("BEFORE:")
        print(json.dumps(comparable(old), indent=2, sort_keys=True))
        print("AFTER:")
        print(json.dumps(comparable(current), indent=2, sort_keys=True))
        return 10
    finally:
        fcntl.flock(f, fcntl.LOCK_UN)
        f.close()

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--site", required=True)
    p.add_argument("--baseline-file", required=True)
    p.add_argument("--ip", default="")
    p.add_argument("--save-baseline", action="store_true")
    a = p.parse_args()
    try:
        return save(a) if a.save_baseline else verify(a)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 20

if __name__ == "__main__":
    sys.exit(main())
