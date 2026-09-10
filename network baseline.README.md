# Network Baseline Verification

## Automated pre/post-reboot network validation for NUC systems

Preboot
  -> Bash shell script
  -> Python
  -> inspect eno1 + dynamic enp150
  -> centralized JSON
  -> reboot
  -> systemd
  -> Bash
  -> Python comparison
  -> if changed, re-run
  -> centralized log

## eno1 mapping

5001 -> 192.168.0.101
5002 -> 192.168.0.102
...
5016 -> 192.168.0.116

The Bash script derives the address from the hostname.

## enp150

No fixed IP is configured. Python discovers its actual IPv4 address and
records it in the baseline. It also records whether it is DHCP/STATIC and
whether the address is in 172.0.0.0/8.

Therefore, a changed dynamic enp150 address is detected when comparing the
full network state. If the intended behavior is to allow the dynamic IP to
change without flagging it, the comparison can instead ignore the exact
enp150 address and compare only its mode/range.

## Install

sudo install -m 0755 network_flow.sh /usr/local/bin/network_flow.sh
sudo install -m 0755 network_baseline.py /usr/local/bin/network_baseline.py
sudo cp network-baseline.service /etc/systemd/system/
sudo cp network-baseline.timer /etc/systemd/system/
sudo systemctl daemon-reload

## Pre-reboot

sudo /usr/local/bin/network_flow.sh \
  --site SJC-NVIDIA-3200-5001 --save-baseline

## Automatic post-reboot

sudo systemctl enable --now network-baseline.timer

## Files

/central/network-baseline/baseline.json
/central/network-baseline/network.log

## Exit codes

0  = baseline saved / no change
10 = change detected
20 = error
