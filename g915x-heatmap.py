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
QUANT     = 12            # min per-channel colour change before a key is re-sent
SWID      = 0x0d          # HID++ software id (any 1..15)
WRITE_FAIL_LIMIT = 5      # consecutive hidraw write failures before forcing a reconnect

# Supported USB product ids (idVendor is always 046d). c356 = wireless/receiver,
# c359 = wired. Both share the LED-id map and HID++ feature set.
VENDOR_ID    = 0x046d
PRODUCT_IDS  = (0xc356, 0xc359)

# Resolved at runtime in setup() — do NOT hard-code, they vary by model/firmware.
DEV     = 0xFF            # HID++ device index (0xFF wireless-via-receiver, 0x01 wired)
IDX8081 = None            # PER_KEY_LIGHTING feature index
IDX8071 = None            # RGB_EFFECTS feature index


class DeviceGone(Exception):
    """Raised when a hidraw write/transport fails enough to mean the node is dead.
    Caught by the outer lifecycle loop, which re-runs device discovery instead of
    exiting. Never raised on a clean shutdown."""

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
# Consecutive write-failure counter. The evdev node alone is not a reliable
# liveness signal: keyd's virtual keyboard keeps the same eventN across a
# keyboard wake while the real hidrawN is replaced, so a frozen daemon would
# otherwise write forever into a dead fd. We count OSErrors on write and force a
# reconnect once they pile up. Any successful write resets the count.
_write_fails = 0

def _reset_write_fails():
    global _write_fails
    _write_fails = 0

def _note_write_fail():
    """Bump the consecutive write-failure counter; raise DeviceGone past the limit."""
    global _write_fails
    _write_fails += 1
    if _write_fails >= WRITE_FAIL_LIMIT:
        raise DeviceGone(f'{_write_fails} consecutive hidraw write failures')

def frame(x):
    b = bytes.fromhex(x) if isinstance(x, str) else bytes(x)
    n = 20 if b[0] == 0x11 else 7
    return (b + bytes(n - len(b)))[:n] if len(b) < n else b[:n]

def xfer(fd, report, timeout=0.4):
    """Write a report, return the first HID++ reply for our device index.
    Returns None on transport failure (no reply / read error)."""
    try:
        os.write(fd, frame(report)); _reset_write_fails()
    except OSError:
        _note_write_fail(); return None
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
    """Fire-and-forget write; drain any reply so the queue doesn't back up.
    Counts write failures and raises DeviceGone once they cross WRITE_FAIL_LIMIT."""
    try:
        os.write(fd, frame(report)); _reset_write_fails()
    except OSError:
        _note_write_fail(); return
    dl = time.time() + 0.02
    while time.time() < dl:
        r,_,_ = select.select([fd], [], [], max(0, dl - time.time()))
        if not r: break
        try: os.read(fd, 64)
        except OSError: break

def get_feature_index(fd, feature_id):
    """IRoot getFeature -> resolved feature index.
    Returns:
      int  -- resolved index from a valid reply (0 means feature genuinely absent),
      None -- no reply / transport failure / error reply (featureIndex 0xff)."""
    rep = xfer(fd, [0x10, DEV, 0x00, (0 << 4) | SWID, (feature_id >> 8) & 0xff, feature_id & 0xff, 0x00])
    if rep is None:
        return None                        # no reply / transport hiccup -> caller retries
    if len(rep) >= 3 and rep[2] == 0xff:
        # HID++ error reply (byte 2 = 0xff on this device; same convention as
        # tools/probe1.py is_error) -> a transport error, NOT "feature absent".
        return None
    if len(rep) >= 5:
        return rep[4]                      # valid reply; 0 == genuinely absent
    return None

# ---------------- device discovery ----------------
def _uevent_matches(text):
    """True if a hidraw uevent is our keyboard: idVendor==046d & idProduct in the
    supported set. Parsed from the HID_ID (bus:vendor:product, hex) or MODALIAS
    (...vVVVVpPPPP...) line — a real id match, not a loose 'C356' substring."""
    vid = pid = None
    for line in text.splitlines():
        if line.startswith('HID_ID='):
            parts = line.split('=', 1)[1].split(':')
            if len(parts) == 3:
                try: vid, pid = int(parts[1], 16), int(parts[2], 16)
                except ValueError: pass
        elif line.startswith('MODALIAS=') and (vid is None or pid is None):
            # hid:bBBBBgGGGGvVVVVVVVVpPPPPPPPP -- vendor & product are 8 hex digits each.
            m = line.split('=', 1)[1]
            i, j = m.find('v'), m.find('p')
            if i != -1 and j != -1 and j > i:
                try: vid, pid = int(m[i+1:i+9], 16), int(m[j+1:j+9], 16)
                except ValueError: pass
    return vid == VENDOR_ID and pid in PRODUCT_IDS

def find_hidraw():
    """Return (path, fd) of the keyboard's vendor HID++ node, and set global DEV.
    Matches by USB id (046d:c356 wireless or 046d:c359 wired), then probes device
    index 0xFF (wireless/receiver) before 0x01 (wired)."""
    global DEV
    for path in sorted(glob.glob('/dev/hidraw*')):
        n = os.path.basename(path)
        try: ue = open(f'/sys/class/hidraw/{n}/device/uevent').read()
        except OSError: continue
        if not _uevent_matches(ue): continue
        try: fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except OSError: continue
        for devidx in (0xFF, 0x01):
            try: os.write(fd, frame([0x10, devidx, 0x00, (1 << 4) | SWID, 0, 0, 0x5a]))
            except OSError: break              # interface rejects writes -> next node
            ok = False; dl = time.time() + 0.4
            while time.time() < dl:
                r,_,_ = select.select([fd], [], [], max(0, dl - time.time()))
                if not r: continue
                try: d = os.read(fd, 64)        # device may be yanked mid-probe
                except OSError: break
                if len(d) >= 4 and d[0] in (0x10, 0x11) and d[1] == devidx:
                    ok = True; break
            if ok:
                DEV = devidx
                return path, fd
        os.close(fd)
    return None, None

def find_evdev():
    # Prefer keyd's virtual keyboard (keyd grabs the real device if you remap the
    # G-keys with it — see README); fall back to the physical G915 X keyboard.
    found = {}
    keyd_seen = False                          # any 'keyd virtual keyboard' node at all
    blk = {}
    try:
        with open('/proc/bus/input/devices') as f:
            for line in f:
                line = line.rstrip('\n')
                if line.startswith('N: Name='): blk['name'] = line.split('=',1)[1].strip('"')
                elif line.startswith('H: Handlers='): blk['h'] = line
                elif line == '':
                    name = blk.get('name',''); h = blk.get('h','')
                    if name == 'keyd virtual keyboard': keyd_seen = True
                    if 'kbd' in h:
                        node = next((t for t in h.split() if t.startswith('event')), None)
                        if node and name == 'keyd virtual keyboard': found['keyd'] = '/dev/input/'+node
                        elif node and name == 'Logitech G915 X LS':   found['g915'] = '/dev/input/'+node
                    blk = {}
    except OSError as e:
        print(f'WARNING: cannot read /proc/bus/input/devices: {e}', file=sys.stderr, flush=True)
        return None
    if found.get('keyd'):
        return found['keyd']
    if found.get('g915') and keyd_seen:
        # keyd is present (a virtual keyboard exists) but we only found the physical
        # node — keyd has very likely grabbed the real device, so reading its evdev
        # gives us NO keypresses and the heatmap silently freezes. Documented footgun.
        print('WARNING: keyd virtual keyboard detected but using the physical G915 X '
              'evdev node — if keyd grabbed the keyboard, keypresses will be invisible '
              'and the heatmap will not update. Point the daemon at the keyd node.',
              file=sys.stderr, flush=True)
    return found.get('g915')

def wait_devices(running=None):
    """Block until both the hidraw and evdev nodes exist. `running` is the shared
    [bool] shutdown flag: a clean SIGTERM/SIGINT while waiting raises DeviceGone so
    the outer loop can exit instead of spinning here forever."""
    announced = False
    while running is None or running[0]:
        hpath, fd = find_hidraw()
        ev = find_evdev() if fd else None
        if fd and ev: return hpath, fd, ev
        if fd: os.close(fd)
        if not announced: print('waiting for G915 X...', flush=True); announced = True
        time.sleep(3)
    raise DeviceGone('shutdown requested while waiting for device')

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
EV_FMT = 'llHHi'; EV_SZ = struct.calcsize(EV_FMT)

def setup(running=None):
    """Discover + open the device and resolve the lighting feature indices.
    Returns (hpath, fd, evfd). Raises DeviceGone on a transport hiccup during
    feature resolution (caller reconnects). Exits 1 only when a feature is a
    genuine, valid 'absent' (resolved index 0) — i.e. truly unsupported hardware."""
    global IDX8081, IDX8071
    hpath, fd, ev = wait_devices(running)
    try:
        idx8081 = get_feature_index(fd, 0x8081)
        idx8071 = get_feature_index(fd, 0x8071)
        if idx8081 is None or idx8071 is None:
            # No reply / error reply / transport failure — link hiccup, not an
            # unsupported device. Bounce through the reconnect path instead of dying.
            os.close(fd)
            raise DeviceGone('feature resolution got no valid reply')
        if idx8081 == 0 or idx8071 == 0:
            print(f'ERROR: required HID++ features missing '
                  f'(0x8081={idx8081:#x}, 0x8071={idx8071:#x}). Unsupported variant?',
                  file=sys.stderr)
            os.close(fd); sys.exit(1)
        IDX8081, IDX8071 = idx8081, idx8071
        evfd = os.open(ev, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    except DeviceGone:
        raise
    except OSError:
        try: os.close(fd)
        except OSError: pass
        raise DeviceGone('failed to open evdev node')
    print(f'hidraw={hpath} evdev={ev} devIdx={DEV:#x} '
          f'PER_KEY=0x{IDX8081:02x} RGB_EFFECTS=0x{IDX8071:02x}', flush=True)
    return hpath, fd, evfd

def cold_fill(fd, wire_ids, heat, last_rgb):
    """Take software control, paint the whole board cold, and render current heat.
    May raise DeviceGone if the node dies during the handshake/fill."""
    host_mode_init(fd)
    cold = heat_color(0.0)
    set_range(fd, 0x01, 0x6f, cold)                          # all main keys + modifiers
    for a,b in STATIC_RANGES: set_range(fd, a, b, cold)      # G-keys, media, indicator, logo
    commit(fd)
    render(fd, wire_ids, heat, last_rgb)

def run_session(fd, evfd, running, code2wire, wire_ids, heat, last_rgb):
    """One device lifecycle: cold-fill then pump events until clean shutdown or
    device loss. Heat/last_rgb persist across calls so a reconnect resumes, not
    resets. Raises DeviceGone on device loss; returns normally on clean shutdown."""
    if not running[0]: return                                # shutdown raced setup -> let main board-off
    _reset_write_fails()
    # last_rgb is reset so the post-reconnect cold-fill + render actually re-sends
    # every key to the fresh node (the old node's accepted colours mean nothing now).
    for w in wire_ids: last_rgb[w] = (-99, -99, -99)
    cold_fill(fd, wire_ids, heat, last_rgb)

    last_tick = time.time()
    while running[0]:
        r,_,_ = select.select([evfd], [], [], TICK)
        if r:
            try: data = os.read(evfd, EV_SZ * 64)
            except OSError: raise DeviceGone('evdev read error')
            if not data: raise DeviceGone('evdev EOF')        # device gone -> reconnect, don't exit
            for off in range(0, len(data) - EV_SZ + 1, EV_SZ):
                _,_,etype,code,val = struct.unpack(EV_FMT, data[off:off+EV_SZ])
                if etype == 1 and val == 1:
                    w = code2wire.get(code)
                    if w is not None: heat[w] = min(1.0, heat[w] + INCREMENT)
        now = time.time()
        if now - last_tick >= TICK:
            # Scale decay by ACTUAL elapsed time so the 25 s half-life holds even
            # when the scheduler delays a tick (fixed-step decay silently slowed it).
            factor = 0.5 ** ((now - last_tick) / HALFLIFE)
            for w in wire_ids: heat[w] *= factor
            render(fd, wire_ids, heat, last_rgb)
            last_tick = now

def board_off(fd):
    """Whole board off — clean-shutdown only. Tolerates a vanished node."""
    try:
        set_range(fd, 0x01, 0x6f, (0,0,0))
        for a,b in STATIC_RANGES: set_range(fd, a, b, (0,0,0))
        commit(fd)
    except (OSError, DeviceGone):
        pass

def main():
    code2wire = build_code2wire()
    wire_ids = sorted(set(code2wire.values()))
    heat = {w:0.0 for w in wire_ids}                         # persists across reconnects
    last_rgb = {w:(-99,-99,-99) for w in wire_ids}

    running = [True]
    def stop(*_): running[0] = False
    signal.signal(signal.SIGTERM, stop); signal.signal(signal.SIGINT, stop)

    # Outer lifecycle loop: each iteration is one full device session. Device loss
    # (evdev EOF/OSError, or N consecutive hidraw write failures into a dead fd)
    # re-runs discovery + handshake + cold-fill instead of exiting, so a wake-from-
    # sleep or a hidrawN swap relights the board without a systemd restart window.
    while running[0]:
        fd = evfd = None
        try:
            _, fd, evfd = setup(running)
            run_session(fd, evfd, running, code2wire, wire_ids, heat, last_rgb)
        except DeviceGone as e:
            _close(fd); _close(evfd)
            if not running[0]: break                         # clean shutdown raced the wait
            print(f'device lost ({e}); reconnecting...', file=sys.stderr, flush=True)
            time.sleep(1)                                    # brief breather before re-probe
            continue                                         # NO board-off here — node is gone
        # Clean shutdown only (signal flipped running[0]): turn the board off.
        board_off(fd)
        _close(fd); _close(evfd)
        break

def _close(fd):
    if fd is not None:
        try: os.close(fd)
        except OSError: pass

if __name__ == '__main__':
    main()
