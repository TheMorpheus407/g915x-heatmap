#!/usr/bin/env bash
# Live view of the Logitech G915 X: every key you press + the HID++ frames on the
# wire, interleaved. Auto-detects the keyboard's USB bus/address and evdev node.
#
#   ~/g915x/watch-hidpp.sh
#
# Tip: `sudo systemctl stop g915-heatmap` first for a quiet stream — the heatmap
# daemon floods the bus (~40 frames/sec). With it stopped, run a probe in another
# terminal (e.g. sudo python3 ~/g915x/probe1.py) and watch the request/replies.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"

sudo modprobe usbmon 2>/dev/null || true

line=$(lsusb | grep -i 'c356' | head -1)
if [ -z "$line" ]; then echo "G915 X (046d:c356) not found on USB. Is it on?"; exit 1; fi
bus=$(echo "$line" | sed -E 's/Bus 0*([0-9]+) Device.*/\1/')
dev=$(echo "$line" | sed -E 's/.*Device 0*([0-9]+):.*/\1/')

echo "G915 X  ->  USB bus $bus, device $dev  (capture iface: usbmon$bus)"
echo "Columns:  KEY = key you pressed (QWERTZ label)   |   HID++ = frame on the wire"
echo "Press keys. Ctrl-C to stop."
echo

# Robust cleanup: kill our root children by name on exit (the script's own
# argv contains neither pattern, so pkill won't hit it).
cleanup() { sudo pkill -f "tshark -i usbmon$bus" 2>/dev/null; sudo pkill -f "$HERE/keywatch.py" 2>/dev/null; }
trap 'cleanup; exit 0' INT TERM EXIT

# stream 1 — keypresses
sudo python3 "$HERE/keywatch.py" 2>/dev/null | stdbuf -oL sed 's/^/KEY    /' &

# stream 2 — HID++ frames (usbhid.data carries them; deep/control decode = Wireshark GUI)
nix-shell -p wireshark-cli --run \
  "sudo tshark -i usbmon$bus -l -Y 'usb.device_address==$dev && usbhid.data' -T fields -e usbhid.data 2>/dev/null" \
  | stdbuf -oL sed 's/^/       HID++  /' &

wait
