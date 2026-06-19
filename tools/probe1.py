#!/usr/bin/env python3
"""G915 X (046d:c356) HID++ probe step 1: find the vendor node, resolve features.
SAFE: only sends IRoot ping + getFeature queries. No lighting changes."""
import os, select, time

NODES = ['/dev/hidraw14', '/dev/hidraw15', '/dev/hidraw16']
DEV = 0xFF
SWID = 0x0f

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
    # HID++2 error: 0xff feature index 0x08/0x8f patterns; classic: byte2==0xff
    return resp is not None and len(resp) >= 3 and resp[2] == 0xff

for p in NODES:
    try:
        fd = os.open(p, os.O_RDWR)
    except OSError as e:
        print(f"{p}: open failed: {e}")
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
