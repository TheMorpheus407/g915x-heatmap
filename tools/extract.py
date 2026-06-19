#!/usr/bin/env python3
"""Extract HID++ frames from USBPcap captures.

Pulls two kinds of host->device HID++ traffic:
  1. CONTROL SET_REPORT (bmRequestType=0x21 bRequest=0x09): the real lighting
     commands G HUB sends. Payload is in the "Setup Data" layer / USB Control
     buffer after the 8-byte setup header.
  2. INTERRUPT OUT reports (endpoint OUT, transfer_type interrupt): the other
     channel HID++ can travel on.

We work off tshark -T json -x raw bytes so we don't depend on dissector
field names that USBPcap doesn't populate.
"""
import sys, json, subprocess

def run_tshark(pcap, display_filter):
    cmd = ["tshark", "-r", pcap, "-Y", display_filter, "-T", "json", "-x"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    if out.returncode != 0 or not out.stdout.strip():
        return []
    return json.loads(out.stdout)

def hexbytes(raw_entry):
    # tshark _raw json entry is [hexstring, start, len, ...]; we want hexstring
    if isinstance(raw_entry, list):
        return raw_entry[0]
    return raw_entry

def parse(pcap):
    # Control OUT class-interface SET_REPORTs: filter on transfer type control,
    # endpoint OUT (dir 0), and the class request. We just grab all control
    # OUT to be safe, then filter by setup bytes.
    flt = ('usb.transfer_type==0x02 && usb.endpoint_address.direction==0')
    pkts = run_tshark(pcap, flt)
    results = []
    for p in pkts:
        layers = p["_source"]["layers"]
        frameno = layers["frame"]["frame.number"]
        # Get the full frame raw hex
        fraw = hexbytes(layers.get("frame_raw"))
        if not fraw:
            continue
        b = bytes.fromhex(fraw)
        # USBPcap pseudoheader: header length (first 2 bytes LE) = pseudoheader size
        # Then for control SET_REPORT the setup block 21 09 .. appears. Find it.
        # Locate the 8-byte setup header pattern for SET_REPORT class-interface.
        idx = b.find(bytes.fromhex("2109"))
        if idx < 0:
            continue
        setup = b[idx:idx+8]
        bmReq = setup[0]; bReq = setup[1]
        wValue = setup[2] | (setup[3] << 8)
        wIndex = setup[4] | (setup[5] << 8)
        wLength = setup[6] | (setup[7] << 8)
        data = b[idx+8: idx+8+wLength]
        results.append({
            "frame": frameno, "kind": "CTRL_SET_REPORT",
            "bmRequestType": f"0x{bmReq:02x}", "bRequest": f"0x{bReq:02x}",
            "wValue": f"0x{wValue:04x}", "wIndex": wIndex, "wLength": wLength,
            "hidpp": data.hex(),
        })
    return results

def parse_interrupt_out(pcap):
    flt = ('usb.transfer_type==0x01 && usb.endpoint_address.direction==0')
    pkts = run_tshark(pcap, flt)
    results = []
    for p in pkts:
        layers = p["_source"]["layers"]
        frameno = layers["frame"]["frame.number"]
        # usbhid data / capdata is the trailing report. Use the usb_raw or
        # look for 10ff/11ff in the frame tail.
        fraw = hexbytes(layers.get("frame_raw"))
        if not fraw:
            continue
        h = fraw
        for tag in ("10ff", "11ff"):
            i = h.rfind(tag)
            if i >= 0 and i % 2 == 0:
                results.append({"frame": frameno, "kind": "INT_OUT",
                                "hidpp": h[i:]})
                break
    return results

if __name__ == "__main__":
    pcap = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "ctrl"
    if mode == "ctrl":
        rows = parse(pcap)
    else:
        rows = parse_interrupt_out(pcap)
    for r in rows:
        if r["kind"] == "CTRL_SET_REPORT":
            print(f"#{r['frame']:>6}  {r['kind']}  wValue={r['wValue']} wIndex={r['wIndex']} wLen={r['wLength']:>2}  HIDPP={r['hidpp']}")
        else:
            print(f"#{r['frame']:>6}  {r['kind']}  HIDPP={r['hidpp']}")
