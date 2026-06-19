#!/usr/bin/env bash
set -euo pipefail
# Live view of the Logitech G915 X: every key you press + the HID++ frames on the
# wire, interleaved. Auto-detects the keyboard's USB bus/address and evdev node.
#
#   sudo tools/watch-hidpp.sh
#
# Prerequisites:
#   - root (sudo) — usbmon capture and reading the evdev node both need it
#   - the usbmon kernel module (the script modprobes it for you)
#   - tshark (Wireshark CLI). On NixOS without it installed, the script falls
#     back to `nix-shell -p wireshark-cli`.
#
# Tip: `sudo systemctl stop g915x-heatmap` first for a quiet stream — the heatmap
# daemon floods the bus (~40 frames/sec). With it stopped, run a probe in another
# terminal (e.g. sudo python3 tools/probe1.py) and watch the request/replies.
HERE="$(cd "$(dirname "$0")" && pwd)"

sudo modprobe usbmon 2>/dev/null || true

line=$(lsusb | grep -i 'c356' | head -1) || true   # no match must not abort under `set -e`/pipefail
if [ -z "$line" ]; then echo "G915 X (046d:c356) not found on USB. Is it on?"; exit 1; fi
bus=$(echo "$line" | sed -E 's/Bus 0*([0-9]+) Device.*/\1/')
dev=$(echo "$line" | sed -E 's/.*Device 0*([0-9]+):.*/\1/')

echo "G915 X  ->  USB bus $bus, device $dev  (capture iface: usbmon$bus)"
echo "Columns:  KEY = key you pressed (QWERTZ label)   |   HID++ = frame on the wire"
echo "Press keys. Ctrl-C to stop."
echo

# Cleanup on exit. The capture workers run as root under sudo and can be blocked
# in a write (so closing the pipe does NOT always SIGPIPE them) — kill them by
# their specific command first, then mop up the local filter PIDs we spawned. The
# patterns are specific enough not to match this script itself.
pids=()
cleanup() {
  [ -n "${bus:-}" ] && sudo pkill -f "tshark -i usbmon$bus" 2>/dev/null || true
  sudo pkill -f "$HERE/keywatch.py" 2>/dev/null || true
  for p in "${pids[@]:-}"; do
    [ -n "$p" ] && kill "$p" 2>/dev/null || true
  done
}
trap 'cleanup; exit 0' INT TERM EXIT

# stream 1 — keypresses
sudo python3 "$HERE/keywatch.py" 2>/dev/null | stdbuf -oL sed 's/^/KEY    /' &
pids+=("$!")

# stream 2 — HID++ frames (usbhid.data carries them; deep/control decode = Wireshark GUI)
tshark_cmd="sudo tshark -i usbmon$bus -l -Y 'usb.device_address==$dev && usbhid.data' -T fields -e usbhid.data 2>/dev/null"
if command -v tshark >/dev/null 2>&1; then
  bash -c "$tshark_cmd" | stdbuf -oL sed 's/^/       HID++  /' &
  pids+=("$!")
elif command -v nix-shell >/dev/null 2>&1; then
  nix-shell -p wireshark-cli --run "$tshark_cmd" | stdbuf -oL sed 's/^/       HID++  /' &
  pids+=("$!")
else
  echo "error: tshark not found. Install wireshark-cli (or tshark) and re-run." >&2
  exit 1
fi

wait || true
