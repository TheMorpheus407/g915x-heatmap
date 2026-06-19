#!/usr/bin/env python3
"""G915 X (046d:c356) HID++ probe step 1: find the vendor node, resolve features.
SAFE: only sends IRoot ping + getFeature queries. No lighting changes.

Usage: sudo python3 tools/probe1.py [-h]
Run as root (needs read/write on the keyboard's /dev/hidrawN)."""
import os, sys, glob, errno, select, time

DEV = 0xFF
SWID = 0x0f
VENDOR_ID   = 0x046d
PRODUCT_IDS = (0xc356, 0xc359)   # c356 = wireless/receiver, c359 = wired

def _uevent_matches(text):
    """True if a hidraw uevent is our keyboard: 046d:c356 (wireless) or
    046d:c359 (wired). Reads the id from the HID_ID/MODALIAS line rather than a
    loose 'C356' substring — mirrors _uevent_matches() in g915x-heatmap.py."""
    vid = pid = None
    for line in text.splitlines():
        if line.startswith('HID_ID='):
            parts = line.split('=', 1)[1].split(':')
            if len(parts) == 3:
                try: vid, pid = int(parts[1], 16), int(parts[2], 16)
                except ValueError: pass
        elif line.startswith('MODALIAS=') and (vid is None or pid is None):
            m = line.split('=', 1)[1]
            i, j = m.find('v'), m.find('p')
            if i != -1 and j != -1 and j > i:
                try: vid, pid = int(m[i+1:i+9], 16), int(m[j+1:j+9], 16)
                except ValueError: pass
    return vid == VENDOR_ID and pid in PRODUCT_IDS

def find_nodes():
    """Glob /dev/hidraw* and return the keyboard's nodes (046d:c356/c359),
    matched by uevent id — not a hard-coded enumeration (which only works on the
    machine it was read on). Mirrors find_hidraw() in g915x-heatmap.py."""
    nodes = []
    for path in sorted(glob.glob('/dev/hidraw*')):
        n = os.path.basename(path)
        try:
            ue = open(f'/sys/class/hidraw/{n}/device/uevent').read()
        except OSError:
            continue
        if _uevent_matches(ue):
            nodes.append(path)
    return nodes

def hx(b):
    return ' '.join('%02x' % x for x in b)

def drain(fd):
    while True:
        r, _, _ = select.select([fd], [], [], 0)
        if not r:
            break
        try:
            os.read(fd, 64)
        except OSError:
            break

def xfer(fd, report, want_len=4):
    """Write report, wait for a HID++ reply addressed to our device index."""
    try:
        os.write(fd, bytes(report))
    except OSError as e:
        print(f"   [write rejected on this node: {e}]")
        return None
    deadline = time.time() + 0.6
    while time.time() < deadline:
        to = max(0, deadline - time.time())
        r, _, _ = select.select([fd], [], [], to)
        if not r:
            continue
        data = os.read(fd, 64)
        if len(data) >= want_len and data[0] in (0x10, 0x11) and data[1] == DEV:
            return data
    return None

def ping(fd, long_form=False):
    if long_form:
        rep = [0x11, DEV, 0x00, (1 << 4) | SWID, 0x00, 0x00, 0x5A] + [0] * 13
    else:
        rep = [0x10, DEV, 0x00, (1 << 4) | SWID, 0x00, 0x00, 0x5A]
    return xfer(fd, rep)

def get_feature(fd, fid, long_form=False):
    body = [(0 << 4) | SWID, (fid >> 8) & 0xff, fid & 0xff, 0x00]
    if long_form:
        rep = [0x11, DEV, 0x00] + body + [0] * 13
    else:
        rep = [0x10, DEV, 0x00] + body
    return xfer(fd, rep)

def is_error(resp):
    # HID++ error reply: byte 2 (feature index field) == 0xff.
    return resp is not None and len(resp) >= 3 and resp[2] == 0xff

if '-h' in sys.argv[1:] or '--help' in sys.argv[1:]:
    print(__doc__)
    sys.exit(0)

NODES = find_nodes()
if not NODES:
    print("G915 X (046d:c356) hidraw node not found. Is the keyboard on / receiver plugged in?",
          file=sys.stderr)
    sys.exit(1)

for p in NODES:
    try:
        fd = os.open(p, os.O_RDWR)
    except OSError as e:
        print(f"{p}: open failed: {e}")
        if e.errno in (errno.EACCES, errno.EPERM):
            print("   hint: run as root, e.g. sudo python3 tools/probe1.py")
        continue
    try:
        drain(fd)
        form = None
        resp = ping(fd, long_form=False)
        if resp:
            form = 'short'
        else:
            resp = ping(fd, long_form=True)
            if resp:
                form = 'long'
        if not resp:
            print(f"{p}: no HID++ ping reply (not the vendor node, or different addressing)")
            continue
        print(f"{p}: HID++ ALIVE via {form} report -> ping reply: {hx(resp[:8])}")
        lf = (form == 'long')
        for fid, name in [(0x0001, 'IFeatureSet'), (0x8071, 'RGB_EFFECTS'),
                          (0x8081, 'PER_KEY_LIGHTING'), (0x4522, 'feat_4522'),
                          (0x8040, 'BRIGHTNESS?')]:
            fr = get_feature(fd, fid, long_form=lf)
            if fr and not is_error(fr) and len(fr) >= 7:
                idx, typ, ver = fr[4], fr[5], fr[6]
                tag = 'ABSENT' if idx == 0 and fid != 0x0001 else f"index=0x{idx:02x} type=0x{typ:02x} ver={ver}"
                print(f"   getFeature(0x{fid:04x}) [{name}] -> {tag}  raw:{hx(fr[:8])}")
            else:
                print(f"   getFeature(0x{fid:04x}) [{name}] -> no/err reply  raw:{hx(fr[:8]) if fr else 'None'}")
    finally:
        os.close(fd)
