# Adapt CEC-Over-LAN to your TV and your computer

Paste everything below the line into an AI agent that can run commands on the computer you want to control
(Claude Code, Codex CLI, Gemini CLI...), with this repository cloned. The agent checks that your system is supported,
installs the receiver, fits the TV app to your TV, and tells you plainly what does not work. It needs you for a few
physical steps (a photo, pressing buttons, switching inputs on the TV).

---

You are setting up CEC-Over-LAN for me. It lets the remote of an Android/Google TV control the Linux computer that is
connected to that TV over HDMI. Read `README.md` first (what it is, the support section, the limits), then work through
the steps below **in order**. Rules:

* One step at a time. After each step say what you did and what you saw, with the command output that proves it.
  Never write "works" about something you did not observe; say "not verified".
* Ask me before: running anything with `sudo`, installing packages, downloading the Android SDK (about 400 MB),
  enabling a GNOME extension, installing the app on the TV, changing anything outside this repository, `~/cec-over-lan/`,
  `~/.config/systemd/user/` and `~/.local/share/gnome-shell/extensions/`.
* The access token (`~/cec-over-lan/token`) is a secret: never print it, never put it in a commit, a log or a message.
  It is written into the APK on purpose, so the APK is mine alone.
* If a step shows that something cannot work on my system or my TV, **stop and tell me** what, why, and what the
  alternatives are. Do not invent a workaround that changes what the project promises.
* Do not guess key codes, package names or button positions. Observe them, or ask me.

## Step 0: is my computer supported?

Supported today: **GNOME Shell 48 on Wayland** (tested on Debian 13, GNOME 48.7). Other desktops are not supported;
KDE Plasma Bigscreen may come later (the pieces that are GNOME-specific are listed in the README). Check:

```bash
gnome-shell --version                      # must say GNOME Shell 48.x (other versions: tell me, see step 2)
loginctl show-session "$(loginctl | awk '/seat/ {print $1; exit}')" -p Type    # wayland expected
systemctl --user is-active pipewire        # the info panel's spectrum needs PipeWire (it is optional)
python3 -c "import evdev, gi; print('evdev and gi ok')"
command -v adb avahi-browse pw-record; python3 -c "import numpy"
ls -l /dev/uinput; id                      # my user must be able to write to /dev/uinput
```

If it is not GNOME, stop here and tell me. List what is missing and what each missing thing costs (for example: no
`avahi-browse` means the port of the TV is not found automatically; no PipeWire means a flat spectrum) and ask which
packages to install.

## Step 1: the receiver

```bash
mkdir -p ~/cec-over-lan ~/.config/systemd/user
cp -r receiver/* ~/cec-over-lan/
cp receiver/tv-remote.service receiver/tv-remote-web.service ~/.config/systemd/user/
```

Ask me for the TV's address and the connection port (TV: Settings, Developer options, Wireless debugging, the IP and
port on the main screen; for the first pairing "Pair device with pairing code" shows another port and a code). Pair
and connect with `adb pair` / `adb connect`; write `TV_TARGET=<ip>:<port>` to `~/cec-over-lan/tv-remote.env`. Android
older than 11 has no pairing: say so, and use `adb connect <ip>:5555` if the TV allows network debugging. Then
`systemctl --user daemon-reload && systemctl --user enable --now tv-remote tv-remote-web` and confirm in
`journalctl --user -u tv-remote` that keys arrive when I press the remote (ask me to press a few).

## Step 2: the GNOME extension

The receiver restores the focus after the dock and applies per-application rules through `desktop/gnome/focuspid@claude`.

```bash
cp -r desktop/gnome/focuspid@claude ~/.local/share/gnome-shell/extensions/
gnome-extensions enable focuspid@claude   # on Wayland the shell must be restarted: ask me to log out and in
gdbus call --session --dest org.claude.FocusPid --object-path /org/claude/FocusPid --method org.claude.FocusPid.Get
```

Its `metadata.json` lists `"shell-version": ["48"]`. On another GNOME version, tell me first; if I agree, add the
version, enable it and report whether the D-Bus call answers. Do not claim it works unless it did.

## Step 3: my remote

Ask me for a well-lit photo of my remote, then follow `receiver/web/remote-prompt.md` exactly: it describes the JSON
to produce. Validate it by posting it to the web UI (`POST /api/remote/model` with the `X-Token` header, or ask me to
paste it in Setup, Your remote). Codes you are not sure of stay `null`; I learn them with "Learn" in the web UI.

## Step 4: my TV (the profile)

The TV app has to know when the computer is on screen, and that depends on the maker. A **TV profile**
(`android/profiles/*.json`, format in `android/gen_config.py`) says which package is in front while an HDMI input is
shown, which package parts mean the broadcast tuner, and which keys the TV always keeps.

1. Collect: `adb -s <target> shell getprop ro.product.model` / `ro.product.manufacturer` / `ro.build.version.release`
   / `ro.build.version.sdk`. Android older than 11 (API 30) or a streaming box (Chromecast, Shield, Fire TV, where the
   computer is not on the box's own HDMI) is **not supported**: stop and say so.
2. Build the app with the closest existing profile, in log-only mode (keys are not blocked):
   ```bash
   cd android
   # download the SDK once (ask me): see "The TV app" in the README
   CEC_HOST=<this computer's LAN address> CEC_TOKEN="$(cat ~/cec-over-lan/token)" ./build.sh
   ./install.sh <TV ip:port> build/cec-over-lan-tv.apk
   ```
   Check `journalctl --user -u tv-remote -o cat | grep '^\[app\]'` for `TV app connected`.
3. Ask me to do these on the TV, one at a time, and wait a few seconds after each:
   (a) show the computer on its HDMI input and close every menu; (b) open the TV's own menu or info panel while the
   computer is shown; (c) press Home; (d) open another HDMI input if I have one in use; (e) watch a broadcast channel
   if the TV has a tuner; (f) go back to the computer.
4. Read what the receiver logged: `journalctl --user -u tv-remote -o cat | grep '^\[window\] fg='`. Each line is
   `fg=<package> focus=yes|no dvb=yes|no gate?=yes|no | <windows>`. From it, decide:
   * `hdmi_host_packages`: the `fg` package in (a) and (f), when `focus=no`. It must be **different** from the
     packages in (c) and from any app the TV launches. If it is the same as the home screen's, the app cannot tell
     the computer from the home screen: stop, tell me, and recommend `--source adb` (the receiver then reads
     `dumpsys hdmi_control` over adb).
   * `tuner_markers`: parts of the package names in (e) that are not there in (a), if any.
   * `keep_keys`: keep the default unless I ask otherwise.
   * Note whether (a) and (d) look the same: then the app cannot tell my HDMI port from another one. That is a
     limit, not a bug; write it in your report.
5. Write `android/profiles/<brand>-<model>.json`, check it with `python3 android/gen_config.py --check android/profiles/<file>.json` (it refuses a bad
   profile), rebuild with `CEC_PROFILE=profiles/<file>.json`, install again, and check that `gate?=yes` appears
   only in the computer-shown state.
6. Turn the key blocking on only after I have confirmed that the gate is right: rebuild with `CEC_CONSUME=true`.
   While the computer is shown, the TV must not react to the remote (no overlays), but **Power, Home and Volume still
   work on the TV**; test that with me. Tell me how to get out if something goes wrong: Home, then the TV's own source
   button.

## Step 5: check, then report

Ask me to press every button and watch the web UI's Key monitor; run through: the computer shown (keys act), a TV menu
open (keys do not reach the computer), Home, standby and wake (does the app reconnect by itself?), a TV reboot. Report:

* what works, with the evidence you saw;
* what you did not verify;
* the limits that apply to my setup (README, "Support and limitations"), and the ones you found;
* the profile file you wrote, so I can share it (it contains no token and no address).

Never push, publish or send anything anywhere without asking me.
