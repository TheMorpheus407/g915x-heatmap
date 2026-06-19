# g915x-heatmap

A real-time **typing heatmap** for the **Logitech G915 X LIGHTSPEED** (`046d:c356`) on Linux.

Each key on the main typing area warms up **blue → cyan → green → yellow → red**
the more you press it, and cools back down over a configurable half-life. The
G-keys, media keys, logo and indicator get a static backlight. The result is a
live thermal map of how you type.

No Linux tool (OpenRGB, g810-led, libratbag, Solaar) can drive this keyboard's
per-key RGB — so this talks the Logitech **HID++ 2.0** protocol straight to the
device. As far as I can tell, this is the only working per-key RGB controller for
the G915 X on Linux.

> Pure Python standard library. One small daemon, one systemd unit, one udev
> rule. No build step, no dependencies.

*(demo gif goes here)*

## Features

- Per-key typing heatmap on the whole main block (letters, numbers, punctuation,
  numpad, arrows, nav cluster, F-row, modifiers).
- Static backlight on the 9 G-keys, the media keys, the logo and the indicator.
- Layout independent — uses positional HID usage codes, so QWERTY / QWERTZ /
  AZERTY all light the physically-correct key.
- Resolves HID++ feature indexes at runtime and auto-detects the device node, so
  it survives firmware differences and re-plugs.
- Runs as a systemd service, auto-starts on boot, ~150 lines, zero deps.
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
sudo systemctl enable --now g915x-heatmap
```

The unit runs as root, which is the simplest way to get the needed
hidraw-write + evdev-read access. To run it as your own user instead, install the
udev rule and add yourself to the `input` group — see
[`udev/99-g915x.rules`](udev/99-g915x.rules).

### NixOS

Drop `g915x-heatmap.py` next to your config and add:

```nix
systemd.services.g915x-heatmap = {
  description = "Logitech G915 X typing heatmap (per-key RGB)";
  wantedBy = [ "multi-user.target" ];
  after = [ "multi-user.target" ];
  serviceConfig = {
    ExecStart = "${pkgs.python3}/bin/python3 ${./g915x-heatmap.py}";
    Restart = "always";
    RestartSec = "5s";
  };
};
```

## Usage

```sh
systemctl start  g915x-heatmap     # heatmap on
systemctl stop   g915x-heatmap     # back to the keyboard's onboard profile
journalctl -eu   g915x-heatmap     # logs (detected node, feature indexes)
```

Tweak the look by editing the constants at the top of `g915x-heatmap.py`
(`INCREMENT`, `HALFLIFE`, `TICK`, `QUANT`, and the `heat_color()` gradient), then
restart the service.

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
- `keywatch.py` — decode G915 X keypresses to readable names (incl. G1–G9).
- `probe1.py` — find the device, ping it, resolve and print its HID++ features.
- `extract.py` — pull HID++ frames out of USBPcap captures (data hides in USB
  control transfers, invisible to the usual tshark fields).

## Credits

Reverse-engineered from G HUB USB captures attached to
[OpenRGB issue #4461](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4461),
cross-checked against the [OpenRGB](https://gitlab.com/CalcProgrammer1/OpenRGB)
G815/G915 controllers, [Solaar](https://github.com/pwr-Solaar/Solaar) (issue
#3137 / PR #3149 for the c356 software-control gate), and
[keyleds](https://github.com/keyleds/keyleds).

## License

MIT — see [LICENSE](LICENSE).
