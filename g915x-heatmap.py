#!/usr/bin/env python3
"""
g915x-heatmap — a typing heatmap for the Logitech G915 X LIGHTSPEED (046d:c356).

The main typing area warms blue(cold) -> cyan -> green -> yellow -> red the more
each key is pressed, cooling over a configurable half-life. The G-keys, media
keys, logo and indicator get a static backlight. The wireless/BT mode-row LEDs
are not individually addressable on this model and are left dark.

No external dependencies (Python stdlib only). Talks the Logitech HID++ 2.0
protocol straight to the keyboard's raw hidraw node — no Linux RGB tool supports
this keyboard. Reads keypresses from the keyboard's evdev node.

Needs read access to the keyboard's /dev/input/eventN and read/write on its
/dev/hidrawN. Easiest: run as root (the shipped systemd unit does). Otherwise see
udev/99-g915x.rules + add yourself to the `input` group.

See PROTOCOL.md for the full reverse-engineered HID++ reference.
MIT licensed. Tested on the wireless c356 via its Lightspeed receiver.
"""
import os, sys, glob, time, select, struct, signal

# ---------------- tunables ----------------
TICK      = 0.05          # render period (s) -> 20 Hz
INCREMENT = 0.34          # heat added per keypress (~3 presses to max)
HALFLIFE  = 25.0          # seconds for a key's heat to halve
DECAY     = 0.5 ** (TICK / HALFLIFE)
QUANT     = 12            # min per-channel colour change before a key is re-sent
SWID      = 0x0d          # HID++ software id (any 1..15)

# Resolved at runtime in setup() — do NOT hard-code, they vary by model/firmware.
DEV     = 0xFF            # HID++ device index (0xFF wireless-via-receiver, 0x01 wired)
IDX8081 = None            # PER_KEY_LIGHTING feature index
IDX8071 = None            # RGB_EFFECTS feature index

# ---------------- key -> LED id maps (confirmed on c356) ----------------
# main keyboard zone: LED id = USB-HID-usage - 0x03 ; HID usage is positional,
# so this is keyboard-layout independent (QWERTY/QWERTZ/AZERTY all fine).
HID = {
 30:0x04,48:0x05,46:0x06,32:0x07,18:0x08,33:0x09,34:0x0a,35:0x0b,23:0x0c,36:0x0d,
 37:0x0e,38:0x0f,50:0x10,49:0x11,24:0x12,25:0x13,16:0x14,19:0x15,31:0x16,20:0x17,
 22:0x18,47:0x19,17:0x1a,45:0x1b,21:0x1c,44:0x1d,                                  # letters
 2:0x1e,3:0x1f,4:0x20,5:0x21,6:0x22,7:0x23,8:0x24,9:0x25,10:0x26,11:0x27,          # digits
 28:0x28,1:0x29,14:0x2a,15:0x2b,57:0x2c,12:0x2d,13:0x2e,26:0x2f,27:0x30,43:0x31,   # enter esc bksp tab space - = [ ] \
 39:0x33,40:0x34,41:0x35,51:0x36,52:0x37,53:0x38,58:0x39,                          # ; ' ` , . / caps
 59:0x3a,60:0x3b,61:0x3c,62:0x3d,63:0x3e,64:0x3f,65:0x40,66:0x41,67:0x42,68:0x43,87:0x44,88:0x45, # F1-F12
 86:0x64,                                                                          # ISO <>| key
 99:0x46,70:0x47,119:0x48,                                                         # PrtSc ScrLk Pause
 110:0x49,102:0x4a,104:0x4b,111:0x4c,107:0x4d,109:0x4e,                            # Ins Home PgUp Del End PgDn
 106:0x4f,105:0x50,108:0x51,103:0x52,                                              # arrows R L Down Up
 69:0x53,98:0x54,55:0x55,74:0x56,78:0x57,96:0x58,                                  # NumLk KP/ KP* KP- KP+ KPEnter
 79:0x59,80:0x5a,81:0x5b,75:0x5c,76:0x5d,77:0x5e,71:0x5f,72:0x60,73:0x61,82:0x62,83:0x63, # KP 1..9 0 .
}
MOD = {42:0xE1,54:0xE5,29:0xE0,97:0xE4,56:0xE2,100:0xE6,125:0xE3,126:0xE7}         # modifier zone: id = HID - 0x78
# Static-backlight id ranges: G-keys G1-G9 = 0xB4-0xBC, media = 0x9B-0x9E,
# indicator = 0x99, logo = 0xD2. (G-keys also type as F13-F21 = keycodes 183-191.)
STATIC_RANGES = [(0xB4,0xBC),(0x9B,0x9E),(0x99,0x99),(0xD2,0xD2)]

def build_code2wire():
    m = {}
    for c,h in HID.items(): m[c] = (h - 0x03) & 0xff
    for c,h in MOD.items(): m[c] = (h - 0x78) & 0xff
    return m

# ---------------- HID++ transport ----------------
def frame(x):
    b = bytes.fromhex(x) if isinstance(x, str) else bytes(x)
    n = 20 if b[0] == 0x11 else 7
    return (b + bytes(n - len(b)))[:n] if len(b) < n else b[:n]

def xfer(fd, report, timeout=0.4):
    """Write a report, return the first HID++ reply for our device index."""
    try: os.write(fd, frame(report))
    except OSError: return None
    dl = time.time() + timeout
    while time.time() < dl:
        r,_,_ = select.select([fd], [], [], max(0, dl - time.time()))
        if not r: continue
        try: d = os.read(fd, 64)
        except OSError: return None
        if len(d) >= 4 and d[0] in (0x10, 0x11) and d[1] == DEV:
            return d
    return None

def hidpp_send(fd, report):
    """Fire-and-forget write; drain any reply so the queue doesn't back up."""
    try: os.write(fd, frame(report))
    except OSError: pass
    dl = time.time() + 0.02
    while time.time() < dl:
        r,_,_ = select.select([fd], [], [], max(0, dl - time.time()))
        if not r: break
        try: os.read(fd, 64)
        except OSError: break

def get_feature_index(fd, feature_id):
    """IRoot getFeature -> resolved index (0 = feature absent)."""
    rep = xfer(fd, [0x10, DEV, 0x00, (0 << 4) | SWID, (feature_id >> 8) & 0xff, feature_id & 0xff, 0x00])
    if rep and len(rep) >= 5 and rep[2] != 0xff:
        return rep[4]
    return 0

# ---------------- device discovery ----------------
def find_hidraw():
    """Return (path, fd) of the c356 vendor HID++ node, and set global DEV.
    Tries device index 0xFF (wireless/receiver) then 0x01 (wired)."""
    global DEV
    for path in sorted(glob.glob('/dev/hidraw*')):
        n = os.path.basename(path)
        try: ue = open(f'/sys/class/hidraw/{n}/device/uevent').read()
        except OSError: continue
        if 'C356' not in ue.upper(): continue
        try: fd = os.open(path, os.O_RDWR)
        except OSError: continue
        for devidx in (0xFF, 0x01):
            try: os.write(fd, frame([0x10, devidx, 0x00, (1 << 4) | SWID, 0, 0, 0x5a]))
            except OSError: break              # interface rejects writes -> next node
            ok = False; dl = time.time() + 0.4
            while time.time() < dl:
                r,_,_ = select.select([fd], [], [], max(0, dl - time.time()))
                if not r: continue
                d = os.read(fd, 64)
                if len(d) >= 4 and d[0] in (0x10, 0x11) and d[1] == devidx:
                    ok = True; break
            if ok:
                DEV = devidx
                return path, fd
        os.close(fd)
    return None, None

def find_evdev():
    blk = {}
    for line in open('/proc/bus/input/devices'):
        line = line.rstrip('\n')
        if line.startswith('N: Name='): blk['name'] = line.split('=',1)[1].strip('"')
        elif line.startswith('H: Handlers='): blk['h'] = line
        elif line == '':
            if blk.get('name') == 'Logitech G915 X LS' and 'kbd' in blk.get('h',''):
                for tok in blk['h'].split():
                    if tok.startswith('event'): return '/dev/input/' + tok
            blk = {}
    return None

def wait_devices():
    announced = False
    while True:
        hpath, fd = find_hidraw()
        ev = find_evdev() if fd else None
        if fd and ev: return hpath, fd, ev
        if fd: os.close(fd)
        if not announced: print('waiting for G915 X...', flush=True); announced = True
        time.sleep(3)

# ---------------- colour / rendering ----------------
def heat_color(h):
    """blue(cold) -> cyan -> green -> yellow -> red(hot)"""
    h = 0.0 if h < 0 else 1.0 if h > 1 else h
    stops = [(0.0,(0,40,255)),(0.30,(0,210,235)),(0.55,(0,235,40)),
             (0.78,(255,225,0)),(1.0,(255,30,0))]
    for i in range(len(stops)-1):
        h0,c0 = stops[i]; h1,c1 = stops[i+1]
        if h <= h1:
            t = (h-h0)/(h1-h0) if h1>h0 else 0
            return tuple(int(c0[j]+(c1[j]-c0[j])*t) for j in range(3))
    return stops[-1][1]

def set_range(fd, first, last, rgb):
    """0x8081 func 5: setRange(firstId, lastId, R, G, B)."""
    hidpp_send(fd, [0x11, DEV, IDX8081, (5 << 4) | SWID, first, last, rgb[0], rgb[1], rgb[2]])

def commit(fd):
    hidpp_send(fd, [0x11, DEV, IDX8081, (7 << 4) | SWID])

def render(fd, wire_ids, heat, last_rgb):
    changed = []
    for wid in wire_ids:
        rgb = heat_color(heat[wid]); pr,pg,pb = last_rgb[wid]
        if abs(rgb[0]-pr) >= QUANT or abs(rgb[1]-pg) >= QUANT or abs(rgb[2]-pb) >= QUANT:
            last_rgb[wid] = rgb; changed.append((wid,)+rgb)
    if not changed: return
    for i in range(0, len(changed), 4):                      # 0x8081 func 1: up to 4 keys/frame
        body = []
        for wid,r,g,b in changed[i:i+4]: body += [wid,r,g,b]
        hidpp_send(fd, [0x11, DEV, IDX8081, (1 << 4) | SWID] + body)
    commit(fd)

def host_mode_init(fd):
    """Take software control of the lighting (0x8071 fn3/fn5/fn7 handshake).
    Without it the edge LEDs reject writes (HID++ error 5)."""
    for h in ('10ff083d000020','10ff085d000000','10ff085d010307',
              '11ff087d010000003c012c00','11ff087d0100000000005a00',
              '10ff085d010305','10ff083d000001'):
        # patch in the resolved 0x8071 index (byte 2) instead of the literal 0x08
        b = bytearray(frame(h)); b[2] = IDX8071
        hidpp_send(fd, bytes(b))

# ---------------- main ----------------
def setup():
    global IDX8081, IDX8071
    hpath, fd, ev = wait_devices()
    IDX8081 = get_feature_index(fd, 0x8081)
    IDX8071 = get_feature_index(fd, 0x8071)
    if not IDX8081 or not IDX8071:
        print(f'ERROR: required HID++ features missing '
              f'(0x8081={IDX8081:#x}, 0x8071={IDX8071:#x}). Unsupported variant?',
              file=sys.stderr); sys.exit(1)
    print(f'hidraw={hpath} evdev={ev} devIdx={DEV:#x} '
          f'PER_KEY=0x{IDX8081:02x} RGB_EFFECTS=0x{IDX8071:02x}', flush=True)
    return fd, ev

def main():
    fd, ev = setup()
    evfd = os.open(ev, os.O_RDONLY | os.O_NONBLOCK)
    code2wire = build_code2wire()
    wire_ids = sorted(set(code2wire.values()))
    heat = {w:0.0 for w in wire_ids}
    last_rgb = {w:(-99,-99,-99) for w in wire_ids}

    running = [True]
    def stop(*_): running[0] = False
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)

    host_mode_init(fd)
    cold = heat_color(0.0)
    set_range(fd, 0x01, 0x6f, cold)                          # all main keys + modifiers
    for a,b in STATIC_RANGES: set_range(fd, a, b, cold)      # G-keys, media, indicator, logo
    commit(fd)
    render(fd, wire_ids, heat, last_rgb)

    EV_FMT = 'llHHi'; EV_SZ = struct.calcsize(EV_FMT)
    last_tick = time.time()
    while running[0]:
        r,_,_ = select.select([evfd], [], [], TICK)
        if r:
            try: data = os.read(evfd, EV_SZ * 64)
            except OSError: break                            # device gone -> exit; service restarts
            if not data: break
            for off in range(0, len(data) - EV_SZ + 1, EV_SZ):
                _,_,etype,code,val = struct.unpack(EV_FMT, data[off:off+EV_SZ])
                if etype == 1 and val == 1:
                    w = code2wire.get(code)
                    if w is not None: heat[w] = min(1.0, heat[w] + INCREMENT)
        now = time.time()
        if now - last_tick >= TICK:
            for w in wire_ids: heat[w] *= DECAY
            render(fd, wire_ids, heat, last_rgb)
            last_tick = now
    # on exit: whole board off
    set_range(fd, 0x01, 0x6f, (0,0,0))
    for a,b in STATIC_RANGES: set_range(fd, a, b, (0,0,0))
    commit(fd)

if __name__ == '__main__':
    main()
