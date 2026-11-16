# CEC-Over-LAN

Use the physical remote of a Google TV as an input device for a Linux VM (or PC) connected to the TV over HDMI, when HDMI-CEC does not carry the keys: the key presses travel over the LAN (adb) and the receiver injects them only while the TV is showing the machine.

How it works (variant A, no app on the TV):

1. The machine connects to the TV with `adb` (wireless debugging, one-time pairing).
2. `receiver/tv_remote.py` reads `adb shell getevent -l`, translates the keys and re-injects them into a `uinput` device ("TV Remote (CEC-Over-LAN)").
3. The machine sees a normal keyboard: ES-DE, Kodi and GNOME react to it.
4. Every 1.5 s the receiver reads `mActiveRoutingPath` from `dumpsys hdmi_control` and injects only if the TV is showing the machine's HDMI port (`0x1000` = HDMI 1, configurable with `--active-path`). When you use the TV for anything else, no key reaches the machine.

Tested on: Debian 13 with GNOME Shell 48.7, a `MiTV_MZTU0` Google TV, a 20 m fibre-optic HDMI cable. The full test report is in [REPORT.md](REPORT.md).

## Default mapping

| Remote | Event read | Injected on the machine |
|---|---|---|
| Arrows | `KEY_UP/DOWN/LEFT/RIGHT` | arrows |
| OK | `KEY_SELECT` | Enter |
| Back | `KEY_BACK` | Backspace |
| Channel up / down | `KEY_CHANNELUP/DOWN` | next / previous media |
| Red | `KEY_STOPCD` | Home (`KEY_HOMEPAGE`) |
| Green (hold 3 s) | `KEY_EJECTCD` | Alt+F4 |
| Yellow | `KEY_SHUFFLE` | Super (GNOME overview) |
| Blue | `KEY_SEARCH` | Play/Pause |

Volume is left to the TV. The physical Home key reaches `getevent` but is not mapped (the TV uses it to leave the input). Mute and Assistant were not observed.

## Requirements

- On the TV: Developer options and Wireless debugging enabled, by hand, by the owner.
- On the machine: `adb` (`android-tools-adb`), Python 3 with `python-evdev`, write access to `/dev/uinput` (via an ACL here).

## Usage

```bash
adb pair <TV_IP>:<PAIRING_PORT> <CODE>      # once
adb connect <TV_IP>:<PORT>
python3 receiver/tv_remote.py --target <TV_IP>:<PORT> --dry-run   # print only
python3 receiver/tv_remote.py --target <TV_IP>:<PORT>             # inject, only while the machine is on screen
python3 receiver/tv_remote.py --target <TV_IP>:<PORT> --no-gate   # always inject
```

As a service: see `receiver/tv-remote.service` (user unit, `Restart=always`).

## Pairing from a phone (web UI)

After a TV reboot or standby the wireless debugging port and pairing are lost (T4/T6). `receiver/tv_remote_web.py` serves a small mobile page on the LAN to redo them from a phone:

```bash
cp receiver/tv-remote.service receiver/tv-remote-web.service ~/.config/systemd/user/
mkdir -p ~/cec-over-lan && cp receiver/tv_remote.py receiver/tv_remote_web.py ~/cec-over-lan/ && cp -r receiver/web ~/cec-over-lan/
echo 'TV_TARGET=<TV_IP>:<PORT>' > ~/cec-over-lan/tv-remote.env
systemctl --user daemon-reload && systemctl --user enable --now tv-remote tv-remote-web
cat ~/cec-over-lan/token          # access token, created on first start (mode 0600)
```

<p align="center"><img src="docs/web-ui.png" alt="The pairing web UI on a phone (IP and ports redacted)" width="320"></p>

Open `http://<machine-ip>:8765/?t=<token>` on the phone once (the page remembers the token). Then: enter the pairing port and code, tap *Pair*, enter the connection port, tap *Connect and restart receiver*. The page also shows the service state, the adb link and whether the machine is on the TV screen.

Security: it answers only to private (RFC 1918) addresses, every API call needs the token, inputs are validated and no shell is used. It is meant for a trusted home LAN: do not forward its port to the internet.

## Roadmap

**Coming next: an Android APK for the TV** that uses an AccessibilityService to capture the remote's keys and send them to the machine. This removes the dependency on wireless debugging, which is switched off by a standby or a reboot (see T4 and T6 in REPORT.md), and with it the need to re-pair by hand. Until then, the web UI above is the stopgap.

## Known limits

- The wireless debugging port can change: after a TV reboot the old port was `offline` (see T6 in REPORT.md), so a new `adb connect` is needed.
- Behaviour with the TV in standby is not verified (T4).
- Key latency is not measured.

## Other files

- `RULES.md`, `PROMPT.md`: the rules and the task given to the agent that ran the tests.
- `STATUS.md`: test status.
