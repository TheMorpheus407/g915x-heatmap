# Logitech G915 X (046d:c356) — HID++ 2.0 RGB protocol reference

Everything below was reverse-engineered from G HUB USB captures (see
[OpenRGB #4461](https://gitlab.com/CalcProgrammer1/OpenRGB/-/issues/4461)) and
**confirmed on real hardware** (the wireless c356 via its Lightspeed receiver),
cross-checked against the OpenRGB G815/G915 controllers, Solaar, and keyleds.

If you have a different Logitech keyboard, the *method* here applies but the
indexes and the LED-id map will differ — resolve everything at runtime.

## Transport

- The keyboard exposes several `hidraw` nodes. The one that speaks HID++ is the
  **vendor interface**: it has `C356` in its `uevent` and answers a root ping.
  The other c356 nodes reject writes with `EPIPE`. Pick the right one by pinging.
- Raw `read`/`write` to that fd. No library needed.
- Reports: `0x10` short (7 bytes) and `0x11` long (20 bytes). Layout:

  ```
  [reportId] [deviceIndex] [featureIndex] [(func<<4)|swid] [params…]
  ```

- **deviceIndex = `0xFF`** for the wireless unit behind its receiver (use `0x01`
  for wired). **swid** = any 1–15 (this project uses `0x0d`); it is echoed back so
  you can match replies. The keyboard accepts the long form for everything.

## Feature discovery (do this at runtime — indexes are not stable)

IRoot (`featureIndex 0x00`), function 0 = `getFeature(featureId)`:

```
request : 10 FF 00 0F  <idHi> <idLo> 00
reply   : 11 FF 00 0F  <resolvedIndex> <featType> <featVer> …
```

On the confirmed unit:

| Feature | ID | Index | Notes |
|---|---|---|---|
| `RGB_EFFECTS` | `0x8071` | `0x08` | firmware effects; 2 clusters only (keyboard, logo) |
| `PER_KEY_LIGHTING` | `0x8081` | `0x09` | the per-key path used here |
| `0x4522` | — | absent | OpenRGB's "managed mode" latch does **not** apply |

## PER_KEY_LIGHTING (`0x8081`) function table

Function byte = `(func << 4) | swid`. With index `0x09`, swid `0x0d`:

| Func | Byte | What |
|---|---|---|
| 1 | `1d` | **little** — set up to 4 individual keys: `11 FF 09 1d [id R G B]×4` |
| 5 | `5d` | **range** — set a contiguous id range one colour: `11 FF 09 5d [first last R G B]×3` |
| 6 | `6d` | **big** — up to 13 ids share one colour: `11 FF 09 6d [R G B][id×13]` |
| 7 | `7d` | **commit** — `11 FF 09 7d 00…` (nothing displays until commit) |

Sets are **additive** to a per-key buffer; `commit` displays the whole buffer.
Entering software/host mode **blanks every LED not in the buffer** — it is a full
takeover, not an overlay on the onboard firmware profile. The onboard profile and
a per-key stream cannot be shown at the same time.

## LED-id map (the `id` byte)

| Group | ids |
|---|---|
| Main keyboard | `USB-HID-usage − 0x03` (positional → layout-independent) |
| Modifiers (LCtrl…RWin) | `0x68`–`0x6F` (`HID 0xE0…0xE7 − 0x78`) |
| Whole main block + modifiers | range `0x01`–`0x6F` |
| G-keys **G1…G9** | `0xB4`–`0xBC` |
| Media: Play/Pause, Mute, Next, Prev | `0x9B`, `0x9C`, `0x9D`, `0x9E` |
| Indicator / brightness | `0x99` |
| Logo | `0xD2` |
| Wireless / Bluetooth / mode row | **no addressable id found** — left dark |

(For input: the G-keys type as **F13–F21** on evdev — G1–G9 = keycodes 183–191.)

## Software-control handshake (required before writing the edge LEDs)

Before streaming `0x8081`, take software control via `0x8071` (otherwise the
edge/media LEDs reject writes with HID++ error 5 — see Solaar #3137):

```
10 FF <8071idx> 3d 00 00 20
10 FF <8071idx> 5d 00 00 00
10 FF <8071idx> 5d 01 03 07
11 FF <8071idx> 7d 01 00 00 00 3c 01 2c 00 …
11 FF <8071idx> 7d 01 00 00 00 00 00 5a 00 …
10 FF <8071idx> 5d 01 03 05
10 FF <8071idx> 3d 00 00 01
```

After this, only stream `0x8081`. **Never** send an `0x8071` effect frame while
streaming per-key — it flips the board back to the onboard firmware profile and
blanks the `0x8081` buffer. To return to the factory lighting, just stop driving
it (the keyboard reverts on its own).

## Whole-board solid colour (firmware effect, for reference)

`0x8071` setMultiLedRangeEffect — lights the main board only (not media/G-keys):

```
11 FF <8071idx> 1d <zone 00|01> <effect 01=static|02=breathing> RR GG BB 02 … 01
```

## Gotcha when capturing

In USBPcap captures, the colour-set commands are **USB control transfers
(SET_REPORT)** — their payload is *not* in `usb.capdata` / `usbhid.data`; read it
from the control transfer (`tshark -x`, or `tools/extract.py`). On Linux `usbmon`,
the same frames show up under `usbhid.data` (see `tools/watch-hidpp.sh`).

## Reading a frame

```
11 ff 09 1d  1a 00 ff 00 …
│  │  │  │   └── id 0x1a, RGB 00ff00 (one of up to four [id R G B] tuples)
│  │  │  └── func 1, swid 0x0d  (little/setIndividual)
│  │  └── feature index 0x09  (= PER_KEY_LIGHTING, resolved at runtime)
│  └── device index 0xFF
└── report id 0x11 (long, 20 bytes)
```
