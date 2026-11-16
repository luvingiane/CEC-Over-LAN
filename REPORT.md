# REPORT.md — Google TV remote as input for a Linux VM

Date: 2026-11-16. TV: model `MiTV_MZTU0` (codename `river`). Addresses: `<TV_IP>`, `<VM_IP>`.
Marking: DONE = command + real output; HYPOTHESIS = not proven.

| Test | Outcome |
|---|---|
| T1 Video streaming | OK |
| T2 adb from the VM | OK |
| T3 getevent, TV on | OK |
| T4 getevent, standby | OK (no network in standby; wireless debugging switches off) |
| T5 Keys and UI | PARTIAL |
| T6 Persistence after reboot | OK (port and pairing do not survive a reboot) |
| T7 Latency | PARTIAL |
| T8 uinput device | OK |
| T9 Mapping | PARTIAL |
| T10 systemd unit | PREPARED, NOT INSTALLED |
| T11 Inject only while the machine is on screen | OK |
| T12 Pairing web UI | OK (confirmed on a phone; auto-detect unverified) |
| T13 Editable key mapping | OK (pending the owner's check on the real remote) |
| T14 Setup wizard and launch actions | OK (pending the owner's check with the real remote) |
| T15 Dry-run switch | OK (pending the owner's test with the real remote) |
| T16 Key monitor screen | OK (pending the owner's check with the real remote) |
| T17 Hide the mouse pointer | PARTIAL (to be confirmed on the TV) |
| T18 Launched programs in their own scope | Fixed and tested; activation waits for a restart |

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
**DONE (commands on the VM, TV put in standby by the owner with the remote):** `ping` to the TV 0% answered (100% loss), `adb connect` -> `No route to host`, `adb devices` kept showing a stale `device` line, `adb shell` on that link -> `error: closed` / timeout. While the TV was in standby no key can reach the VM.
**DONE (monitor log, 3 s interval):** `16:16:27 ping=down`, `16:16:47 ping=up` — the TV answers the ping again about 20 s after being woken by the remote.
**DONE (after wake-up):** `adb connect` on the same port -> `offline` / timeout; `adb shell` -> `device offline`. **Reported by the owner:** Wireless debugging is also switched off by the standby. So after a standby the connection port and the pairing have to be redone, exactly as after a reboot (T6).
**DONE (journal):** the receiver process stayed `active (running)` but did not notice that the link was dead and logged nothing. Fixed with a watchdog in `receiver/tv_remote.py` (after `--dead-after` failed source reads, default 4, the adb link is killed and re-established, with a stale-entry `adb disconnect` first).
**I don't know:** whether keys reach `getevent` while the TV is in standby with wireless debugging still on (not possible here, it turns off), whether the TV's remote wake-up key produces a readable event.
**Outcome:** OK (answer: with the TV in standby the machine cannot receive keys, and wireless debugging is lost). This is a second argument for variant B.

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
**DONE:** after the reboot the old port did not answer (`ping` to the TV 2/2 received, TCP connect to the old port timed out, `adb mdns services` found nothing). A new `adb pair` on a new pairing port succeeded (same `guid`, i.e. the same TV and key), then `adb connect` on a new connection port gave `device`. So after a TV reboot **both the connection port and the pairing changed/were lost**: a new pairing and a new port are needed every time.
**Consequence:** variant A cannot reconnect by itself after a TV reboot; a human has to read the new port and code on the TV. This is the main argument for variant B (an app on the TV that connects out to the machine).
**Outcome:** OK (answer: the port and the pairing do not survive a reboot).

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

## T12 — Pairing web UI (stopgap for variant A)
**Why:** after a standby or reboot the pairing and the port are lost (T4/T6); doing it on the machine shown on the TV is awkward, so it is done from a phone.
**Done:** `receiver/tv_remote_web.py` + `receiver/web/index.html` (stdlib only), `receiver/tv-remote-web.service`; the receiver now runs from `receiver/tv-remote.service` with its target read from `~/cec-over-lan/tv-remote.env`. Both are user units, enabled.
**DONE (tests from another LAN machine):** page served (HTTP 200); API without token -> 401; with token `/api/status` returns the service, link and target; a public IP, a port with shell characters, a short code and an unknown service action are all rejected; the page JavaScript passes `node --check`.
**DONE (owner's test on a phone):** the page works; the screenshot (`docs/web-ui.png`, IP and ports redacted) shows `Receiver service: active`, `TV link (adb): device`, `Machine on TV screen: yes` after pairing and connecting from the page. The pairing and connection steps themselves were not captured step by step.
**Not tested:** the mDNS auto-detect button (it found nothing with wireless debugging off; not retried).
**Risks:** anyone on the LAN who has the token can pair/connect adb and restart the service; the token is stored in `~/cec-over-lan/token` (0600). Do not expose the port to the internet.
**Outcome:** OK (confirmed by the owner on a phone); auto-detect still unverified.

## T13 — Editable key mapping (web UI)
**Request:** map the keys from the web UI, numbers included; two more keys pressed by the owner (Exit and TTX) do nothing on the TV in HDMI mode.
**DONE (service journal):** unmapped keys seen: `KEY_EXIT` (Exit) and `KEY_FIND` (HYPOTHESIS: this is TTX; the log does not say which physical key was pressed), plus a single `KEY_POWER` (the power key).
**Done:** the mapping is now a file (`~/cec-over-lan/keymap.json`) reloaded by the receiver within a second; per key a **tap** action and/or a **long** action (with a hold time), either one a `+` combo. The web UI has a *Key mapping* card: keys seen in the last 90 s as chips, editable rows, *Add numbers 0–9*, *Add key by name*, *Save*, *Reset to defaults*. Every `KEY_*` code is registered on the uinput device at start so the mapping can change without recreating it. Power-type targets are refused.
**DONE (tests):** 13 offline unit tests pass (`python3 -m unittest discover -s tests`): validation, tap held between DOWN and UP, combos released in reverse, long press fires once, tap+long on the same key, source-inactive injects nothing, release after source loss, reload and a broken file keeping the previous mapping, key code coverage (numbers present, power absent).
**DONE (bug found by testing):** `python-evdev` returns some key names as tuples; the first version crashed on them. Fixed in `receiver/tv_remote.py` and `receiver/tv_remote_web.py`.
**DONE (browser, 375x812):** the page renders without horizontal scroll or console errors; *Add numbers 0–9* adds 10 rows; saving a mapping with `KEY_POWER` as a target is rejected, without `KEY_POWER` it is saved (`keymap.json`, mode 0600, expected content); deployed to the machine, both services `active`, `[keymap] loaded 12 keys from defaults`.
**Not tested:** pressing the new keys on the real remote after deployment, typing on a real phone, whether this remote sends digits as `KEY_0`–`KEY_9` (earlier a `KEY_8` was seen once, so probably yes: HYPOTHESIS).
**Default:** `KEY_EXIT` and `KEY_FIND` are not mapped by default; the owner chooses what they do in the UI.
**Outcome:** OK, pending the owner's check on the real remote.

## T14 — Setup wizard and launch actions (apps, websites)
**Request:** an installer-style flow that first lets the user press all the buttons without recording anything, then records them against ready-made command lists (ES-DE's list already existed), and the ability to bind a key to an application or a website.
**Done:** a four-step *Setup wizard* in the web UI (discover, assign, shortcuts, review/save), command lists in `receiver/web/profiles.json` (ES-DE: 17 commands from the user guide, Desktop and media: 12, Number keys: 10), and two new action types next to key combos: `app:<file>.desktop` (launched with `gio launch`) and `url:http(s)://…` (opened with `xdg-open`). New API endpoints: `/api/apps`, `/api/events`, `/api/profiles`. The receiver launches only once per press (1.5 s debounce) and only while the machine is on the TV screen.
**Design choice (security):** no free-form command is accepted. Apps must be installed `.desktop` files (the id is validated and resolved against the known application directories, so no path traversal) and URLs must be `http(s)`; the process is started without a shell, detached, and with the graphical-session variables of `systemd --user`. The price: anyone with the web UI token can launch any installed app or open any web address on the machine.
**DONE (tests):** 19 offline unit tests pass, including: `javascript:`, `file:`, `ftp:`, spaces/newlines in URLs, `../` and `$()` in app ids, shell text as an action are all rejected; an app tap launches once and is debounced; a long press can launch a URL while the tap presses a key; nothing launches when the source is inactive.
**DONE (browser, 375x812, simulated key presses through a test hook):** discover step counts presses and enables *Next* only after the first key; the assign step handles a conflict (a key already used for the same kind of action is refused and the command stays pending), skip, and a long-press command (Quit, hold 3 s); the shortcut step rejects an invalid address and accepts a website on tap and an application on long press; review and save wrote `keymap.json` (mode 0600) with the expected content, and the receiver's own loader reads it back. The server also rejects an application that is not installed, a path traversal, `javascript:`/`file:` URLs and a free-form command.
**DONE (on the machine):** `/api/apps` lists 72 applications including `esde.desktop`, `kodi.desktop` and `firefox-esr.desktop`; the three profiles load; the service environment contains `DISPLAY=:0`, `WAYLAND_DISPLAY=wayland-0` and `XDG_RUNTIME_DIR`; a dry run resolves `kodi.desktop` to `/usr/share/applications/kodi.desktop`; both services are `active`.
**Not tested:** a real launch of an application or a page on the machine from the remote (only dry runs, to avoid opening windows on the TV unannounced); the wizard with the real remote (the key events there were simulated); other desktops than GNOME.
**Outcome:** OK, pending the owner's check with the real remote.

## T15 — Dry-run switch
**Request:** when a remote button is pressed, only do a dry run and apply no change.
**Done:** a runtime flag file (`~/cec-over-lan/dry-run`) re-checked every 0.5 s by the receiver; while it exists keys are not pressed and apps/URLs are not launched, they are only logged. A key already pressed when the switch is turned on is still released (no stuck keys). The web UI has a *Dry run* card with the switch and the list of the latest actions (dry-run lines read "would …", real launches "LAUNCHED").
**DONE (tests):** 24 offline unit tests pass; the new ones cover the flag following the file, no key pressed while dry, keys pressed when not dry, a key already down released after switching to dry, and the permanent `--dry-run`.
**DONE (API, local test server):** `POST /api/dryrun {"on": true}` creates the flag file, `false` removes it, a non-boolean is rejected; `/api/status` reports `dry_run`.
**DONE (on the machine):** switched on; both services `active`; the page served is the updated one.
**DONE (journal, found while checking):** at 18:32:10 and 18:33:49 (machine time) the service really launched ES-DE and Kodi after `KEY_0`/`KEY_1` presses on the real remote, with the mapping saved from the web UI at 18:32 and 18:33; no error was logged. This shows that digits reach the receiver as `KEY_0`–`KEY_9` and that app launching from a key works; the log does not say whether the windows appeared.
**Not tested:** the dry-run list with real key presses after the switch (the owner was about to try).
**I don't know:** why the receiver's cgroup showed a 1.1 GB memory peak and about 3 minutes of CPU in roughly an hour before the last restart; it now uses about 15 MB.
**Outcome:** OK, pending the owner's test with the real remote.

## T16 — Key monitor screen
**Request:** a screen that shows the pressed button, to analyse better whether the remote still reaches the machine for commands issued off-screen.
**Done:** every event is logged as `[seen] KEY_X DOWN|UP screen=yes|no decision=… dry=… / held=… result=…` and the web UI has a *Key monitor* card (start/stop, clear, big button name, on/off-screen badge, decision, a history with off-screen rows in red and an "only off-screen" filter). New endpoint `/api/monitor`. Backward compatible: the wizard feed and the recent-keys chips still work with the new line format.
**DONE (tests):** 28 offline tests pass, including: off-screen presses are flagged and never applied; decision/held/result fields; long-press results (`long-fired`, `short-press-no-action`); URLs reduced to the host so a log line never contains spaces; the web parser reads the new format, tolerates the old one and keeps the wizard feed working.
**DONE (browser, 375x812, simulated events):** the big display, the ON SCREEN / OFF SCREEN badge, the "[dry run]" marker, the counters (5 events, 2 off-screen, 1 unmapped) and the off-screen filter behave as expected; no horizontal scroll. The first layout was a table that broke on a phone and was replaced by two-line rows.
**DONE (on the machine, journal):** after the restart the new format is in the log (`[seen] KEY_0 DOWN screen=no decision=off-screen dry=no`).
**Caveat found:** right after a restart the gate state is unknown for about a second, so a button pressed in that moment is logged as `screen=no` even if the machine is on screen (that `KEY_0` was followed by `machine on screen: yes`).
**What it can and cannot tell:** it shows that the remote's events reach the receiver off-screen and that they are ignored; it cannot tell whether the TV itself also reacted to the button (that is the TV's behaviour, seen on the TV).
**Also observed (web log):** from the same LAN device the mapping was saved at 18:12, 18:32 and 18:33 and the dry-run switch was turned off at 18:46:04; real launches of ES-DE followed.
**Outcome:** OK, pending the owner's check with the real remote.

## T17 — Hide the mouse pointer on remote presses
**Request:** when a remote button is pressed the mouse pointer should disappear.
**Done:** a second virtual mouse moves the pointer to the bottom-right corner (5 relative moves of 5000 on each axis, at most one burst every 0.4 s) on every remote press while the machine is on the TV screen; it does nothing in dry run, and a web switch (*Mouse pointer* card, file `pointer-visible`) or `--no-hide-pointer` turns it off. Chosen because a GNOME Shell extension would need a session restart on Wayland (it would have closed ES-DE and Kodi), and `gsettings` has no per-keypress hiding.
**DONE (tests):** 35 offline tests pass; the pointer ones check the move direction (positive X and Y), the throttle, the off switch, dry run, a missing device, and that the log shows `pointer=moved` only for presses while on screen (also for unmapped buttons).
**DONE (on the machine):** the device "TV Remote pointer mover (CEC-Over-LAN)" exists; the journal shows `pointer=moved` on a real `KEY_0` press.
**Not verified:** that the pointer is really out of sight on the TV (I cannot see the screen), and the behaviour with several monitors or with a dock that reacts to the bottom-right corner.
**Outcome:** PARTIAL until the owner confirms on the TV.

## T18 — Programs launched from the remote lived inside the receiver's cgroup
**Found while looking at "1.3 GB peak and 3 minutes of CPU" in the service stop log (twice).** `systemctl --user status tv-remote` listed ES-DE and YouTube TV among the service's processes (113 tasks, 851 MB) and `/proc/<es-de>/cgroup` pointed to `tv-remote.service`. So the memory and CPU were the launched applications, **not a leak**; but it also means that **restarting or stopping the receiver closes every application it launched** (this happened at least once during a deployment).
**Fix:** `scope_cmd()` runs `gio launch` / `xdg-open` through `systemd-run --user --scope`, so each program gets its own scope.
**DONE (on the machine, inside a transient service that mimics the receiver):** the service cgroup was `cec-scope-test*.service`, the program's `run-p…scope`; after the service ended the program was still running and its scope `active running`. A first attempt looked like the program died: it had inherited the closed service's `--pipe`; the real code redirects stdio to `/dev/null` and was re-tested that way.
**Not yet active:** the running receiver still has the old code until it is restarted, and restarting it closes the applications launched before (they are in its cgroup). New launches after that restart are safe.
**Outcome:** fixed in code and tested; activation waits for a restart.

## Recommendation (provisional)
Variant A (no app on the TV) works, but T6 and T4 showed that after a TV reboot or a standby the wireless debugging port and the pairing are lost, so it needs a manual re-pairing each time. It is fine as a stopgap. The planned next phase is variant B: an APK with an AccessibilityService that captures the keys and sends them to the machine, which removes the dependency on wireless debugging.

## To do
T10, key-latency measurement, confirm Backspace in ES-DE, Kodi, install the systemd unit, variant B (APK + AccessibilityService).

## Proposals
- Long press for other keys.
- Automatic reconnection by discovering the port through mDNS (`adb mdns services`), to be verified.
- A `--raw` mode that injects a unique code per physical key, so a tool such as Input Remapper can bind each key freely (not implemented; Input Remapper not verified).
