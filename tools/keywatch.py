#!/usr/bin/env python3
"""Print which G915 X key is pressed (key-down only), with a timestamp.
Auto-finds the keyboard's evdev node. Labels reflect the physical QWERTZ key.

Usage: sudo python3 tools/keywatch.py [EVDEV_NODE] [-h]
Pass an explicit /dev/input/eventN to override auto-detection."""
import os, sys, struct, select, time

# evdev keycode -> physical key label (German QWERTZ; Y/Z swapped vs QWERTY)
NAMES = {
 1:'Esc',
 2:'1',3:'2',4:'3',5:'4',6:'5',7:'6',8:'7',9:'8',10:'9',11:'0',12:'ß',13:'´',14:'Backspace',15:'Tab',
 16:'Q',17:'W',18:'E',19:'R',20:'T',21:'Z',22:'U',23:'I',24:'O',25:'P',26:'Ü',27:'+',28:'Enter',
 29:'LCtrl',30:'A',31:'S',32:'D',33:'F',34:'G',35:'H',36:'J',37:'K',38:'L',39:'Ö',40:'Ä',41:'^',
 42:'LShift',43:'#',44:'Y',45:'X',46:'C',47:'V',48:'B',49:'N',50:'M',51:',',52:'.',53:'-',
 54:'RShift',55:'KP*',56:'LAlt',57:'Space',58:'CapsLock',86:'<>|',
 59:'F1',60:'F2',61:'F3',62:'F4',63:'F5',64:'F6',65:'F7',66:'F8',67:'F9',68:'F10',87:'F11',88:'F12',
 69:'NumLock',70:'ScrollLock',119:'Pause',99:'PrtSc',
 71:'KP7',72:'KP8',73:'KP9',74:'KP-',75:'KP4',76:'KP5',77:'KP6',78:'KP+',
 79:'KP1',80:'KP2',81:'KP3',82:'KP0',83:'KP.',96:'KPEnter',98:'KP/',
 97:'RCtrl',100:'AltGr',125:'LWin',126:'RWin',127:'Menu',
 102:'Home',103:'Up',104:'PgUp',105:'Left',106:'Right',107:'End',108:'Down',109:'PgDn',110:'Insert',111:'Delete',
 183:'G1',184:'G2',185:'G3',186:'G4',187:'G5',188:'G6',189:'G7',190:'G8',191:'G9',
}

def find_node():
    # Prefer keyd's virtual keyboard: when the G-keys are remapped with keyd
    # (the repo's flagship feature) keyd GRABS the physical device and re-emits
    # events on "keyd virtual keyboard", so the G-keys only appear there.
    # Fall back to the physical keyboard otherwise. Mirrors find_evdev() in
    # g915x-heatmap.py.
    found = {}
    blk = {}
    for line in open('/proc/bus/input/devices'):
        line = line.rstrip('\n')
        if line.startswith('N: Name='): blk['n'] = line.split('=',1)[1].strip('"')
        elif line.startswith('H: Handlers='): blk['h'] = line
        elif line == '':
            name = blk.get('n', ''); h = blk.get('h', '')
            if 'kbd' in h:
                node = next((t for t in h.split() if t.startswith('event')), None)
                if node and name == 'keyd virtual keyboard': found['keyd'] = '/dev/input/' + node
                elif node and name == 'Logitech G915 X LS':   found['g915'] = '/dev/input/' + node
            blk = {}
    return found.get('keyd') or found.get('g915')

args = [a for a in sys.argv[1:] if a not in ('-h', '--help')]
if len(args) != len(sys.argv[1:]):
    print(__doc__)
    sys.exit(0)

node = args[0] if args else find_node()
if not node:
    print('keyboard evdev node not found', file=sys.stderr); sys.exit(1)
fd = os.open(node, os.O_RDONLY)
FMT = 'llHHi'; SZ = struct.calcsize(FMT)
while True:
    r, _, _ = select.select([fd], [], [], None)
    data = os.read(fd, SZ * 64)
    for off in range(0, len(data) - SZ + 1, SZ):
        _, _, etype, code, val = struct.unpack(FMT, data[off:off+SZ])
        if etype == 1 and val == 1:                       # EV_KEY, key-down
            print('%s  %s' % (time.strftime('%H:%M:%S'), NAMES.get(code, 'KEY_%d' % code)), flush=True)
