# g915x-heatmap

A real-time **typing heatmap** for the **Logitech G915 X LIGHTSPEED** (`046d:c356`) on Linux.

Each key on the main typing area warms up **blue → cyan → green → yellow → red**
the more you press it, and cools back down over a configurable half-life. The
G-keys, media keys, logo and indicator get a static backlight. The result is a
live thermal map of how you type.

I'm not aware of any other tool that drives the G915 X's per-key RGB on Linux —
the usual suspects (OpenRGB, g810-led, libratbag, Solaar) don't, so this talks
the Logitech **HID++ 2.0** protocol straight to the device. See [Credits](#credits)
for the cross-references.

> Pure Python standard library. One small daemon, one systemd unit, one udev
> rule. No build step, no dependencies.

> A typing heatmap is, by nature, a key-frequency side channel — a visible board
> or a screen-share reveals which keys you use most. The daemon itself does not
> log keystrokes; it only keeps a per-LED heat counter that decays over time.

## Features

- Per-key typing heatmap on the whole main block (letters, numbers, punctuation,
  numpad, arrows, nav cluster, F-row, modifiers).
- Static backlight on the 9 G-keys, the media keys, the logo and the indicator.
- Layout independent — uses positional HID usage codes, so QWERTY / QWERTZ /
  AZERTY all light the physically-correct key.
- Resolves HID++ feature indexes at runtime and auto-detects the device node, so
  it survives firmware differences and re-plugs.
- Runs as a systemd service, auto-starts on boot, zero deps.
- Tunable: heat-per-press, cool-down half-life, refresh rate, and the colour
  gradient are all constants at the top of the script.

## Requirements

- A Logitech **G915 X LIGHTSPEED** (USB ID `046d:c356`). See [Compatibility](#compatibility).
- Linux with `hidraw` and `evdev` (any modern kernel; Wayland or X11 — irrelevant,
  it works below the display server).
- Python 3 (standard library only).

## Install

### Generic Linux (systemd)

```sh
git clone https://github.com/TheMorpheus407/g915x-heatmap
cd g915x-heatmap
sudo install -m 0755 g915x-heatmap.py /usr/local/bin/g915x-heatmap.py
sudo install -m 0644 systemd/g915x-heatmap.service /etc/systemd/system/
sudo install -m 0644 systemd/g915x-heatmap-resume.service /etc/systemd/system/
sudo systemctl enable --now g915x-heatmap
sudo systemctl enable g915x-heatmap-resume      # re-light after suspend/resume
```

The unit runs as root, which is the simplest way to get the needed
hidraw-write + evdev-read access. To run it as your own user instead, install the
udev rule and add yourself to the `input` group — see
[`udev/99-g915x.rules`](udev/99-g915x.rules).

> **Privacy trade-off of the `input` group.** The udev rule's `uaccess` tag
> already grants the keyboard's hidraw node to the logged-in user, so no extra
> group is needed for the lighting writes. The `input` group is only for the
> evdev keypress reads — and it grants that user (and *every* process it runs)
> read access to **all** system input devices, i.e. system-wide keylogging
> capability. Running as your own user is not strictly "safer than root"; it
> moves the trust, it doesn't remove it.

### NixOS

Drop `g915x-heatmap.py` next to your config and add:

```nix
systemd.services.g915x-heatmap = {
  description = "Logitech G915 X typing heatmap (per-key RGB)";
  wantedBy = [ "multi-user.target" ];
  after = [ "multi-user.target" "keyd.service" ];   # keyd ordering only matters if you use keyd for G-key macros
  serviceConfig = {
    ExecStart = "${pkgs.python3}/bin/python3 ${./g915x-heatmap.py}";
    Restart = "always";
    RestartSec = "5s";
  };
};
```

For the fully-hardened unit plus the suspend/resume hook and the keyd G-key
setup as one block, see
[`deploy/configuration-g915.nix`](deploy/configuration-g915.nix).

## Usage

```sh
systemctl start  g915x-heatmap     # heatmap on
systemctl stop   g915x-heatmap     # back to the keyboard's onboard profile
journalctl -eu   g915x-heatmap     # logs (detected node, feature indexes)
```

Tweak the look by editing the constants at the top of `g915x-heatmap.py`
(`INCREMENT`, `HALFLIFE`, `TICK`, `QUANT`, and the `heat_color()` gradient), then
restart the service.

## Troubleshooting

On a healthy start the daemon logs one line like:

```
hidraw=/dev/hidraw3 evdev=/dev/input/event7 devIdx=0xff PER_KEY=0x09 RGB_EFFECTS=0x08
```

Use that line to read off what it found. `journalctl -eu g915x-heatmap` shows it.

- **Keyboard not detected** (it logs `waiting for G915 X...` and the start line
  never appears). Confirm the receiver is the c356: `lsusb | grep -i c356`. If
  it's absent, it's a different model/receiver and not supported here. If it's
  present but the daemon still waits, check `journalctl -eu g915x-heatmap` —
  most likely it lacks read/write on the hidraw node (run as root, or install
  the udev rule).
- **Lights, but no heat** (the board lights up cold blue and never warms on
  keypress). The lighting half works but the keypress reads don't — the daemon
  found the hidraw node but not a usable `evdev=` node, or can't read it. If
  running as your own user, add yourself to the `input` group; otherwise check
  the `evdev=` path on the start line exists and is readable.
- **G-keys, media keys or the wireless/mode row stay dark.** That's by design,
  not a bug. The G-keys, media keys, logo and indicator get a *static*
  backlight (they don't heat-map), and the wireless/Bluetooth/brightness "mode
  row" LEDs are not individually addressable on this model — see
  [Compatibility](#compatibility).
- **Started before keyd** (heat works on most keys but a keyd remap isn't
  reflected, or the wrong evdev node was picked). The daemon prefers keyd's
  virtual keyboard but only at startup; if it raced keyd, just restart it:
  `systemctl restart g915x-heatmap`.
- **Board dark (or stuck on the onboard profile) after suspend/resume.** The
  keyboard drops its software-controlled lighting across sleep. Installing and
  enabling `g915x-heatmap-resume.service` (see Install) repaints it on resume;
  otherwise just `sudo systemctl restart g915x-heatmap`.

## Compatibility

| Variant | Status |
|---|---|
| G915 X LIGHTSPEED, **wireless via its receiver** (`046d:c356`) | **Tested, works.** |
| G915 X, **wired** (`046d:c359`) | Untested. The daemon falls back to device index `0x01`; the LED-id map should match. Reports welcome. |
| Original G915 / G915 TKL / G815 | Different feature indexes and id layout — would need its own map. Not supported here (use OpenRGB). |

The daemon resolves feature indexes at runtime and tries both HID++ device
indexes, so it should adapt to firmware revisions of the c356. The one thing
genuinely specific to the X is the **9 G-keys** (LED ids `0xB4–0xBC`).

The wireless/Bluetooth/brightness "mode row" LEDs are **not individually
addressable** on this model and are left dark by design.

## Bonus: G-key macros (via keyd)

The G-keys type as plain function keys — **G1–G9 = F13–F21** (keycodes 183–191) —
so they're bindable like any key. For simple actions, bind F13–F21 in your
desktop's shortcut settings. For held modifiers, chords or macros, use
[keyd](https://github.com/rvaiya/keyd) (evdev-level, works on Wayland).

keyd *grabs* the keyboard and re-emits on a "keyd virtual keyboard" device — this
daemon already prefers that device automatically, so the heatmap keeps working
(just start it after keyd). Example (NixOS) — make **G5 cycle browser tabs**:

```nix
services.keyd = {
  enable = true;
  keyboards.g915x = {
    ids = [ "k:046d:c356" ];
    settings.main = { f17 = "C-tab"; };   # G5 (F17) -> Ctrl+Tab
  };
};
```

The bare id `046d:c356` would also grab the keyboard's phantom mouse interface;
the `k:` prefix scopes keyd to the keyboard device only.

G-key → keycode: `G1=f13 G2=f14 G3=f15 G4=f16 G5=f17 G6=f18 G7=f19 G8=f20 G9=f21`.

## How it works

The keyboard speaks Logitech **HID++ 2.0** over a vendor hidraw interface. The
daemon resolves the `PER_KEY_LIGHTING` (`0x8081`) and `RGB_EFFECTS` (`0x8071`)
feature indexes, takes software control of the lighting, then streams per-key
colours: a range-fill for the static backlight, and small 4-key frames at ~20 Hz
for the live heat updates. Keypresses are read from the keyboard's evdev node and
mapped to LED ids by a confirmed offset (`LED id = HID usage − 0x03` for the main
block).

The full reverse-engineered reference — frame formats, the function table, the
complete LED-id map, and how it was derived — is in **[PROTOCOL.md](PROTOCOL.md)**.

## Tools

In [`tools/`](tools/):

- `watch-hidpp.sh` — live view of the HID++ frames on the wire **and** which key
  you pressed, side by side. Great for understanding the protocol.
  *Needs root, plus `usbmon` and `tshark` (Wireshark's CLI).*
- `keywatch.py` — decode G915 X keypresses to readable names (incl. G1–G9).
- `probe1.py` — find the device, ping it, resolve and print its HID++ features.
  *Needs root.*
- `extract.py` — pull HID++ frames out of USBPcap captures (data hides in USB
  control transfers, invisible to the usual tshark fields). *Needs `tshark`.*

## Uninstall

```sh
sudo systemctl disable --now g915x-heatmap
sudo systemctl disable g915x-heatmap-resume
sudo rm /usr/local/bin/g915x-heatmap.py
sudo rm /etc/systemd/system/g915x-heatmap.service
sudo rm /etc/systemd/system/g915x-heatmap-resume.service
sudo systemctl daemon-reload

# only if you installed the udev rule for running as a non-root user:
sudo rm /etc/udev/rules.d/99-g915x.rules
sudo udevadm control --reload
```

On NixOS, remove the `systemd.services.g915x-heatmap` block (and the `keyd`
config if you added it) and rebuild.

## Credits

Reverse-engineered from G HUB USB captures attached to
[OpenRGB issue #4461](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4461),
cross-checked against the [OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB)
G815/G915 controllers, [Solaar](https://github.com/pwr-Solaar/Solaar) (issue
#3137 / PR #3149 for the c356 software-control gate), and
[keyleds](https://github.com/keyleds/keyleds).

## License

MIT — see [LICENSE](LICENSE).
