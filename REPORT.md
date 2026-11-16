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
| T18 Launched programs in their own scope | Fixed, tested and active since the restart (T22) |
| T19 Back in GNOME and ES-DE (per-app exceptions) | OK (pending the owner's check) |
| T20 Reaching the dock | PARTIAL (single-tap `Ctrl+Alt+Tab` not verified) |
| T21 On-screen keyboard | PARTIAL (typing not tried yet) |
| T22 Restart without closing launched apps | OK |
| T23 Key names people type (Super+S) | Fixed and tested; effect of the shortcut to be confirmed |
| T24 Dock popping up on remote presses | Fixed in code and tested; to be confirmed on the TV |
| T25 On-screen keyboard full width, flush bottom | OK (pending the owner's look on the TV) |
| T26 Phone UI: tabs, drawn remote, binding sheet | OK (pending the owner's use on the phone) |
| T27 Esc gives the focus back after the dock | OK (pending the owner's check) |
| T28 Bottom bar invisible on the phone | Fixed and measured; to be seen on the iPhone |
| T29 Remote button icons in Steam | OK on the Big Picture home; rest to be checked |

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

## T19 — Back works in GNOME and in ES-DE (per-app exceptions)
**Problem:** GNOME wants `Esc` to go back, ES-DE wants Back = `Backspace` and uses `Esc` for its main menu; the owner had worked around it with `Backspace` on tap and `Esc` on a 2-second hold.
**Done:** a key may carry exceptions for specific applications (`"apps": {"es-de": {...}}`), matched against the focused window's class, application id, title and process name (case-insensitive substring); the focused window comes from the existing `focuspid` GNOME Shell extension through `gdbus` (cached 0.25 s). The entry chosen at the press is the one used at the release. The web editor shows, edits and saves the exceptions (they are not lost when saving), the Key monitor shows `app: …` for each press, and the log lines carry `app=` and `@<rule>`.
**Applied to the owner's mapping (backup kept):** `KEY_BACK` = `Esc` on tap, with an exception `es-de` -> `Backspace`; the old hold-for-`Esc` was dropped.
**DONE (tests):** 56 offline tests pass, including: other apps get the default, the exception matches the process name / class / title / app id in any case, no focus information falls back to the default, the release uses the entry chosen at the press, the log shows the app and the rule, invalid rules are rejected, saving keeps the rules and they read back.
**DONE (browser):** an existing exception is shown, a second one added from the page, and the saved file has both.
**DONE (on the machine):** `focuspid` answers (`YouTube TV` was focused); the ES-DE process name is `es-de`, which is what the exception matches.
**Not verified:** pressing Back in ES-DE and in GNOME with the real remote after the change; the window class of ES-DE (the rule relies on the process name).
**Outcome:** OK, pending the owner's check.

## T20 — The dock cannot be reached with the arrows
**DONE (read on the machine):** dash-to-dock registers the dock in GNOME's system-controls switcher (`docking.js:1432`, `ctrlAltTabManager.addGroup`), so `Ctrl+Alt+Tab` can give it the keyboard focus (GNOME binding `switch-panels = ['<Control><Alt>Tab']`); `Super+Q` only shows the dock with numbers for 2 s (`shortcut-timeout`, no focus); `hot-keys = true` makes `Super+1…9` launch or switch to the Nth dock icon. The quick settings menu is `Super+S` (`toggle-quick-settings`).
**Done:** the *GNOME shell and dock* list in the wizard (14 actions).
**Not verified:** `Ctrl+Alt+Tab` injected as one quick press (the switcher is normally used while holding the modifiers); the fallback is `Super+1…9`.
**Outcome:** PARTIAL: shortcuts identified and offered; the single-tap focus needs a check.

## T21 — On-screen keyboard for the remote
**Done:** `receiver/tv_osk.py` (GTK 3 window + a model without GTK), action `osk:toggle`, `OskController` in the receiver: it starts the keyboard in its own systemd scope and, while it is open, routes up/down/left/right/OK/Back to it over an abstract UNIX socket instead of injecting them. Types through its own uinput device. Italian and US tables.
**DONE (tests):** 56 offline tests pass: model (wrap-around, vertical moves follow the horizontal position, one-shot capital, symbol layer, special keys), every character has an entry in both layouts and every key name is a real evdev name, the typist presses modifiers around the key (`A` = Shift+A, `/` = Shift+7 on `it`), the controller routes only while the process is alive and only the D-pad, the router does not inject the arrow while the keyboard is open, never starts it in dry run.
**DONE (on the machine):** the window is visible at the requested place (bottom centre, 1285x424 on a 1712x963 screen); the focused window is the same before, during and after (`YouTube TV`); the layout `it` is detected from GNOME; it closes on `close`; the highlighted key follows the arrows. Two visual bugs found and fixed: dimmed text because the window never has the focus (GTK `:backdrop` state), and the user's own GTK theme overriding the highlight (the styles now have a priority above the user's theme).
**Not verified:** typing real text (needs a text field and the owner), every Italian punctuation key, behaviour with several monitors, holding an arrow to repeat.
**Outcome:** PARTIAL until it has been tried by typing.

## T22 — Restarting the receiver closed the launched applications (follow-up of T18)
The T18 fix only applies to applications launched after the receiver restarts with the new code; ES-DE and YouTube TV started by the old code were still in the receiver's cgroup. The receiver was restarted with `KillMode=process` for that single restart (a temporary drop-in, removed afterwards; `KillMode` is `control-group` again) so that they stayed open; systemd logs a "left-over process" warning for them.
**DONE:** both applications still running after the restart, the service active with the new code.

## T23 — "KEY_META+KEY_S does not work"
**What happened (DONE, from the machine's journal and keymap):** the combination never reached the receiver: the saved mapping has no entry with `Super+S` and the log has no such press. `KEY_META` is not a key name in Linux input (Super is `KEY_LEFTMETA`), so the save was refused, and the refusal said only "invalid tap action" without naming the key or the right one. The shortcut itself had been given to the owner as "Super+S" (read from `gsettings`: `toggle-quick-settings = ['<Super>s']`), without the exact name the tool needs, and without ever having been exercised through the receiver.
**Done:** key names are now accepted the way people write them (`Super+S`, `KEY_META+KEY_S`, `Ctrl+Alt+Tab`, `alt+f4`, `escape`, any case, spaces around `+`) and stored in the canonical form; refusals say which name is wrong and suggest the right one (`unknown key 'KEY_LEFTMETAA' (did you mean KEY_LEFTMETA, …)`), and name power keys as not allowed.
**DONE (tests):** 60 offline tests pass, including the exact string that failed (`KEY_META+KEY_S`), the canonical form being what gets saved, and the things that must still be refused (`rm -rf ~`, `KEY_POWER`, `Alt+Fx9`, 5 keys).
**DONE (on the machine):** the web service (restarted alone, so the launched applications stayed open) accepts `KEY_META+KEY_S`, `Super+S` and `Ctrl+Alt+Tab`.
**Inconclusive:** whether an injected `Super+S` opens the quick settings menu. I injected it (held 150 ms and instantly) from a test virtual keyboard and compared the focused window, but the quick settings menu does not change `focus_window`, so the measurement cannot tell. The events are injected without error; whether the menu opens was not observed (I cannot see the TV). A possible cause to rule out: the focused application (YouTube TV) may inhibit compositor shortcuts.
**Outcome:** the name problem is fixed and tested; the effect of the shortcut is to be confirmed by the owner.

## T24 — "When I press Esc the dock must go away"
**DONE (read on the machine):** the dock is at the bottom, auto-hide on, `extend-height = true` (full width), `require-pressure-to-show = true`; dash-to-dock reveals the dock when the pointer pushes against the bottom edge (`docking.js:1102-1106`, a pressure barrier) and keeps it while the pointer hovers it (`_hoverChanged`).
**Cause (my own feature, T17):** the pointer hider parked the cursor in the **bottom-right** corner, i.e. pushed it against that edge on every remote press (Back/Esc included): the dock appeared and, with the cursor still over it, never hid. This is the most likely explanation of the report; it was not observed on the TV.
**Done:** the default parking corner is now **top-right** (`--pointer-corner` to change it), away from the dock and from GNOME's hot corner (top-left).
**DONE (tests):** 61 offline tests pass, including that the default corner moves right and **up** (not down) and that each of the four corners moves the right way.
**DONE (on the machine):** receiver restarted with `KillMode=process` for that restart only (temporary drop-in removed, `KillMode=control-group` again); YouTube TV stayed open (ES-DE had been closed by the owner before the restart).
**Second case, not changed:** after `Ctrl+Alt+Tab` the dock gets the keyboard focus (`_onAccessibilityFocus` unsets the window focus and shows the dock) and dash-to-dock has no `Escape` handling at all, so `Esc` alone does not return the focus. If that is what the owner means, it needs a separate fix (for example sending a key that returns the focus to a window after `Esc`).
**Outcome:** fixed in code and tested; to be confirmed on the TV.

## T25 — On-screen keyboard: full width, no border below
**Request (after trying it on the TV, photo):** the keyboard should have no border below and extend to the width of the TV. The photo also shows that it works: it appears over ES-DE and ES-DE keeps the focus.
**Done:** the panel is as wide as the monitor and flush with its bottom edge (no bottom margin); the keys stay centred. A first attempt stretched every row (and so every key) to the screen width; the owner said the key size had been right, so the keys went back to their original size (`key_unit()`: 0.66/22 of the screen width per unit, a letter key is 2 units) and only the panel is wide.
**DONE (tests):** 63 offline tests pass, including that a letter key is 102 px on a 1712 px screen (the size the owner approved), that the unit has a readable minimum, and that the widest row fits on 1280, 1712, 1920 and 3840 px screens.
**DONE (on the machine):** panel 1712x404 at (0, 559) on a 1712x963 screen, so its bottom is at 963, the screen's bottom edge; keys block 1253 px plus margins = the 1285 px of the first version (`unit=51`); the focused window is the same before, during and after (ES-DE, then Steam Big Picture). Also seen: the window class of ES-DE is `es-de`, which matches the Back exception of T19.
**Provenance question from the owner:** the keyboard (model, tables, GTK window, receiver link) was written from scratch in this project; no third-party keyboard code was copied or adapted and nothing was downloaded for it; it depends on GTK 3 (PyGObject) and `python-evdev` as libraries. The Italian punctuation table comes from the author's knowledge of the xkb `it` layout and is unverified.
**Not changed:** a TV with overscan can still hide the last pixels at the edges; that is a TV setting.
**Outcome:** OK, pending the owner's look on the TV.

## T26 — Phone UI: tabs, a drawn remote and a binding sheet without typing
**Request:** the page had become one very long menu and typing on a phone is awkward; a keyboard/pad UI for the bindings was asked for, with a photo of the remote.
**Done:** (1) four tabs with a bottom bar and a status strip (Remote, Setup, Monitor, Settings); the existing cards were moved, not rewritten. (2) a drawn remote built from `receiver/web/remote.json` (41 buttons laid out like the photo: numbers, INFO/TTX/EPG/AUDIO/source, six app buttons, the D-pad ring with OK, settings/back/home, volume and channel rockers with EXIT/MUTE, and R/G/Y/B); a button lights up when pressed on the real remote. (3) a binding sheet per button: tap or long press (with a hold stepper), action by taps (key picker with modifier toggles and pages, app search list, website presets, on-screen keyboard, nothing), a scope row for per-app exceptions, Save and Remove. (4) **Learn**: the code of a drawn button is learned from a real press and stored in `remote-learned.json`; new endpoint `/api/remote` (GET the model, POST learn or reset).
**DONE (tests):** 68 offline tests pass; the new ones check that the model is consistent (no two buttons share a code, every code is a valid key name), that the codes agree with what the logs showed (R/G/Y/B = STOPCD/EJECTCD/SHUFFLE/SEARCH, EXIT = KEY_EXIT, unknown ones are `null`, never invented), and that learning, resetting, moving a code to another button and rejecting bad input work.
**DONE (browser, 375x812, simulated key presses):** the pad shows 41 buttons, 5 with a binding, 13 unknown and 10 guessed; the sheet for Back shows its current action and the `es-de` exception; `Super` + `S` tapped for the red button was saved as `KEY_LEFTMETA+KEY_S` without touching the other entries (including the Back exception); Learn on INFO took the next real press (`KEY_INFO`), updated the pad and the sheet and wrote `remote-learned.json` (mode 0600); all four tabs render, the wizard still starts, the live feed stops when the Remote tab is left; no console errors. Two defects found there and fixed: the header wrapped on two lines (the status pills are now short) and the keypad page `123` was listed before `Abc`.
**DONE (on the machine):** files deployed, the web service restarted alone (the receiver was not touched), the model loads there (41 buttons).
**Not verified:** the layout on a real phone (only a 375x812 emulation), Learn with the real remote, the guessed and unknown codes (`guess`/`null` in `remote.json`).
**Outcome:** OK, pending the owner's use on the phone.

## T27 — Esc after the dock does not give the focus back to the application
**Problem (owner):** after opening the dock, Esc does not bring the focus back to the application on screen; the arrows keep driving GNOME.
**DONE (measured on the machine, with a test virtual keyboard):** focused window before = `es-de`; after `Ctrl+Alt+Tab` = **no window** (`pid=0`) — which also shows that the combination works as one quick tap; after `Esc` = still no window (the problem reproduced); after `ActivatePid(<es-de pid>)` of the `focuspid` extension = `es-de` again.
**Done:** the receiver remembers the last window that really had the focus and, when a remote button sends `Esc` while no window has it, calls `ActivatePid` for that window 0.25 s later (not in dry run, not if nothing was ever seen). A bug found by the tests and fixed: the focus used to be read after the key had been handled, i.e. a stale value.
**DONE (tests):** 75 offline tests pass: the focus goes back to the window seen before, nothing happens while a window has the focus, with no window ever seen or no focus information, for other keys, and in dry run; through the router with a simulated remote press.
**DONE (on the machine):** receiver restarted with `KillMode=process` for that restart only; ES-DE and YouTube TV stayed open.
**Not verified:** the whole chain with the real remote (Esc pressed on the remote after the dock); a case where several windows are open and the last focused one is not the one on screen.
**Outcome:** OK, pending the owner's check.

## T28 — Bottom bar invisible on the phone (found from the owner's screenshot)
**Cause (DONE, measured):** the new navigation bar had the class `bar`, already used by the wizard's progress bar (`height: 8px; overflow: hidden`), so it was 8 px high instead of 55 and its buttons were clipped. My earlier tests checked that the tabs worked but I never looked at the bar in an image. **Fix:** class renamed to `navbar`; `viewport-fit=cover` and a safe-area padding for iPhones. **DONE (browser 375x812):** 55 px high, fixed at the bottom after scrolling, four tabs visible, the last card clear of it. Not verified on the real iPhone after the fix.

## T29 — Remote button icons in Steam's Big Picture
**Request:** a patch for Steam so that the button icons match the bindings.
**DONE (on the machine):** Steam is a native Debian install (`~/.steam/debian-installation`); its UI is a web app (`steamui`, key names in `sp.js`). The `.cef-enable-debugging` file did **not** open the debug port; the command-line option `-cef-enable-debugging` (a string found in `steamclient.so` and `chromehtml.so`) does (port 8080, Chrome 126). A launcher run as a transient service killed Steam when the launcher exited; `KillMode=none` fixed it. Steam was shut down with SIGTERM to its client (`steam -shutdown` and `steam://ExitSteam` had no effect).
**DONE (live DOM):** the hints are `.KeyCap` elements (text in `.Inner`) inside `KeyboardChordGlyph`; the readable class names are stable, the hashed ones are not. Steam was in Italian: the Enter key cap reads **"Invio"**, found only by looking (`--dump`).
**Done:** `receiver/steam/steam_glyphs.py` (map from the keymap and the remote model + an injected script with a MutationObserver that restores the keys when a binding goes away), `cdp.py` (stdlib DevTools client), `steam-glyphs.service`.
**DONE (tests):** 83 offline tests pass; the map tests cover Back sent as Esc, colour buttons on F1/F9, the Steam exception winning over others, long presses and combinations skipped, the priority between two buttons on one key, and the Italian "Invio".
**DONE (real screen, captured from Steam through the debug port):** the footer shows a red **R** for MENU (F1), a blue **B** for OPZIONI (F9), **OK** for SELEZIONA (Invio) and **←** for INDIETRO (Esc).
**Installed on the machine:** the watcher service (active, 12 glyphs). The owner's own `~/.local/share/applications/steam.desktop` already existed; after a backup (`steam.desktop.bak-20261116-203110`) its main `Exec` and the Big Picture `Exec` got `-cef-enable-debugging`, so Steam keeps the debug port. Revert: restore the backup.
**DONE (restart, owner asked "Riavvia"):** Steam shut down with SIGTERM and started again through the owner's own `steam.desktop` (`gio launch`): the debug port opened after about 6 s and the watcher re-injected the icons by itself (`waiting for Steam` then `installed`). One form of the command did not start Steam at all (`steam -cef-enable-debugging steam://open/bigpicture` run as a transient unit): the launcher's main `Exec` is the one verified. Steam then showed the Big Picture window.
**Not verified:** the Italian names of the other keys (only Invio is confirmed), other Steam screens than the home footer, a reboot of the machine.
**Outcome:** OK on the home screen of Big Picture; the rest to be checked by the owner.

## Recommendation (provisional)
Variant A (no app on the TV) works, but T6 and T4 showed that after a TV reboot or a standby the wireless debugging port and the pairing are lost, so it needs a manual re-pairing each time. It is fine as a stopgap. The planned next phase is variant B: an APK with an AccessibilityService that captures the keys and sends them to the machine, which removes the dependency on wireless debugging.

## To do
T10, key-latency measurement, confirm Backspace in ES-DE, Kodi, install the systemd unit, variant B (APK + AccessibilityService).

## Proposals
- Long press for other keys.
- Automatic reconnection by discovering the port through mDNS (`adb mdns services`), to be verified.
- A `--raw` mode that injects a unique code per physical key, so a tool such as Input Remapper can bind each key freely (not implemented; Input Remapper not verified).
