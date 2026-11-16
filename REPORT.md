# REPORT.md — Google TV remote as input for a Linux VM

Date: 2026-11-16. TV: model `MiTV_MZTU0` (codename `river`). Addresses: `<TV_IP>`, `<VM_IP>`.
Marking: DONE = command + real output; HYPOTHESIS = not proven.

| Test | Outcome |
|---|---|
| T1 Video streaming | OK |
| T2 adb from the VM | OK |
| T3 getevent, TV on | OK |
| T4 getevent, standby | NOT TESTED |
| T5 Keys and UI | PARTIAL |
| T6 Persistence after reboot | PARTIAL (old port offline; new port not tested yet) |
| T7 Latency | PARTIAL |
| T8 uinput device | OK |
| T9 Mapping | PARTIAL |
| T10 systemd unit | PREPARED, NOT INSTALLED |
| T11 Inject only while the machine is on screen | OK |

## T1 — How the VM's video reaches the TV
**Command:** question to the owner.
**Real output:** "20 m HDMI over fibre" (fibre-optic HDMI cable, 20 m).
**Outcome:** OK. DONE: the video arrives over direct HDMI, no streaming. The foreground app is the TV's HDMI input, so no app can forward the keys to the VM: **the sender is needed** (D1 closed).
**HYPOTHESIS:** HDMI-CEC as a way to forward the keys was not verified: it depends on the VM's graphics card and on the fibre cable.
**Risks:** none.

## T2 — adb reachable from the VM
**Command:** `adb pair <TV_IP>:<pairing_port> <code>`, `adb connect <TV_IP>:<port>`, `adb devices -l` (run on the VM).
**Real output:** `Successfully paired to ...`, `connected to ...`, `device product:river model:MiTV_MZTU0 device:river`.
**Outcome:** OK. DONE.
**Risks:** the VM now has an ADB key authorised on the TV. It is revoked in the TV's Developer options ("Revoke USB debugging authorisations"). Wireless debugging is reachable from the LAN while it is enabled.

## T3 — getevent with the TV on
**Commands:** `adb shell ls -l /dev/input`, `adb shell cat /proc/bus/input/devices`, `adb shell getevent -p`, `adb shell getevent -lt` (20 s).
**Real output:** 5 devices (`event0`–`event4`); `/proc/bus/input/devices` denied to the `shell` user ("Permission denied"); `getevent -p` shows the names: "Xiaomi RC Keyboard" (event4), "Xiaomi RC Consumer Control" (event3), "MTK PMU WOBT", "MTK Smart TV IR Receiver", "MTK TV KEYPAD". The key events all arrived from `event3`.
**Outcome:** OK. DONE.
**Note:** this version of `getevent` accepts a single device at a time; without an argument it reads all of them.
**Risks:** read-only.

## T4 — getevent in standby
**Outcome:** NOT TESTED. I don't know whether `adb` stays reachable and whether the keys arrive with the TV in standby. To do: the owner puts the TV in standby, then `adb devices`, `adb connect`, `getevent -lt`.

## T5 — Keys and consumption by the UI
**DONE (from the logs):** codes seen: `KEY_UP/DOWN/LEFT/RIGHT`, `KEY_SELECT`, `KEY_BACK`, `KEY_VOLUMEUP/DOWN`, `KEY_CHANNELUP/DOWN`, `KEY_SEARCH`, `KEY_STOPCD`, `KEY_EJECTCD`, `KEY_SHUFFLE`, `KEY_8`. Every press produces `DOWN` and then `UP`.
**DONE (owner's test):** Green held 3 s -> `Alt+F4` combo in dry-run; short tap -> no effect.
**DONE (owner's test):** Red = `KEY_STOPCD`, Green = `KEY_EJECTCD`, Yellow = `KEY_SHUFFLE` (Yellow opens the GNOME overview), Blue = `KEY_SEARCH`. `KEY_8` is not tied to a known key.
**DONE (service journal):** the physical Home key reaches `getevent` as `KEY_HOMEPAGE` (`[ignored] KEY_HOMEPAGE DOWN/UP`), even though the TV uses it to leave the input.
**I don't know:** Play/Pause, Mute and Assistant did not appear in the logs; I don't know whether the TV consumes them before the kernel. `getevent` reads at kernel level, so it does not prove the TV does not react to the same key.
**Outcome:** PARTIAL.

## T6 — adb persistence after reboot
**DONE:** before the reboot `adb` was connected on port `36465` and the pairing was registered. After the owner rebooted the TV, `adb devices` showed `<TV_IP>:36465 offline` and `adb connect` hung until the timeout.
**DONE (journal):** the receiver crashed with `subprocess.TimeoutExpired` on `adb connect`; systemd restarted it 9 times. Fixed in `receiver/tv_remote.py` (the timeout is now caught and the connection is retried with backoff).
**I don't know:** the new wireless debugging port after the reboot, whether the pairing is still valid, whether a new authorisation is needed.
**Outcome:** PARTIAL. Next: read the new port on the TV (Developer options -> Wireless debugging) and run `adb connect`.

## T7 — Latency
**DONE:** there is no video stream to measure (direct HDMI). The key-path latency (TV -> adb -> VM -> uinput) was **not measured**. The owner reports that the keys work; there is no number.
**Outcome:** PARTIAL.

## T8 — uinput device
**Commands (on the VM):** `ls -l /dev/uinput`, `getfacl /dev/uinput`, `python3 -c "import evdev"`, `grep -A4 "TV Remote" /proc/bus/input/devices`.
**Real output:** `/dev/uinput` is `root:root` with an ACL `user:<user>:rw-`; `python-evdev` importable; device "TV Remote" created as a keyboard.
**Outcome:** OK. DONE. No permission change or installation was needed.
**Risks:** the device injects real keys on the VM (GNOME and ES-DE receive them).

## T9 — Mapping
**Owner's choice (DONE):** arrows -> arrows; OK -> Enter; Back -> Backspace; Channel up/down -> next/previous media (`KEY_NEXTSONG`/`KEY_PREVIOUSSONG`); `KEY_SEARCH` -> Play/Pause; `KEY_STOPCD` -> Home (`KEY_HOMEPAGE`); `KEY_EJECTCD` -> `Alt+F4` after 3 s; `KEY_SHUFFLE` -> `KEY_LEFTMETA` (tap = GNOME overview).
**DONE (VM, GNOME Shell 48.7):** `gsettings get org.gnome.mutter overlay-key` -> `'Super'`; `switch-applications` -> `['<Super>Tab', '<Alt>Tab']`. Alt+Tab from a single key would only toggle between the last two apps, so the overview was chosen (arrows + Enter to pick). The owner confirms it works.
**DONE (source: ES-DE `USERGUIDE.md`):** `USERGUIDE.md:651` arrows, `:661-663` Esc = main menu (not Back), `:686` Enter = A, `:691` "Back key" = B (does not name Backspace), `:705` `Alt+F4` quits.
**DONE (owner's test):** ES-DE reacts to the injected keys ("it works").
**HYPOTHESIS:** Backspace as ES-DE's B key: to be confirmed. WASD does not work in ES-DE by default (not mentioned in the guide); discarded in favour of the arrows.
**Not done:** Kodi (no profile yet), Input Remapper not tried (not installed on the VM).
**Outcome:** PARTIAL.

## T10 — systemd unit
**Done:** `receiver/tv-remote.service` (user unit, `Restart=always`, `RestartSec=2`). **Not installed as a file.**
**DONE:** the receiver runs as a transient user service (`systemd-run --user --unit=tv-remote -p Restart=always -p RestartSec=2 ...`), `systemctl --user is-active tv-remote` -> `active`, device created. It does not survive a reboot of the VM.
**I don't know:** behaviour at boot, restart time after killing the process.
**Risks:** the wireless debugging port can change (see T6). Without `loginctl enable-linger` the service starts only with a login.

## T11 — Inject only while the TV shows the machine
**Problem:** without a check the receiver injects the keys even when the TV is used for something else (home, other apps, other inputs).
**DONE (`adb shell dumpsys hdmi_control` and `dumpsys tv_input`):**
- Machine on HDMI 1: `mActiveRoutingPath: 0x1000`, `mActiveSource: (invalid, invalid)`, foreground app `com.mitv.livetv`.
- TV home: `mActiveRoutingPath: 0x0000`, `mActiveSource: (0x00, 0x0000)`, foreground app the launcher.
- `tv_input` keeps the `HW2` session even outside HDMI 1: it is **not** a reliable signal.
- An Apple TV on HDMI 2 shows up as a CEC device at address `0x2000`.
**Solution:** `tv_remote.py` reads `mActiveRoutingPath` every 1.5 s (options `--active-path`, `--poll`, `--no-gate`) and injects only if it equals `0x1000`. If the read fails it does not inject. When leaving HDMI 1 it releases all keys.
**DONE (journal):** `15:58:07 path=0x1000 -> yes`, `15:58:16 path=0x0000 -> no` (about 2 s after the Home key), `15:58:34 path=0x1000 -> yes`. The owner confirms that on the home screen the machine does not react and on HDMI 1 it does.
**I don't know:** value with the Apple TV in the foreground (HYPOTHESIS: `0x2000`); behaviour with the TV in standby; load of the polling on the TV.
**Outcome:** OK.

## Recommendation (provisional)
Variant A (no app on the TV) works. Before making it permanent: T6 (port stability) and T4 (standby). If the port changes at every reboot, variant A needs a manual re-pairing or reconnection and variant B becomes preferable.

## To do
T4, T6, T10, key-latency measurement, confirm Backspace in ES-DE, Kodi, install the systemd unit.

## Proposals
- Long press for other keys.
- Automatic reconnection by discovering the port through mDNS (`adb mdns services`), to be verified.
- A `--raw` mode that injects a unique code per physical key, so a tool such as Input Remapper can bind each key freely (not implemented; Input Remapper not verified).
