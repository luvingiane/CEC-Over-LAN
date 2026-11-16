# PROMPT.md — Feasibility analysis and tests: Google TV remote as input for VM 170

Read `RULES.md` first. The rules in it are binding and override this document.
Do not run anything yourself: propose one step at a time, the owner runs it and pastes the output.

## 1. Context

- **VM 170** is a Debian VM on Proxmox: a gaming/multimedia box. Its video output is shown on the owner's **Google TV**.
- The **physical remote of the Google TV** currently controls the TV, not the VM.
- It is not yet known **how the VM's video reaches the TV** (Moonlight, Steam Link, Kodi, something else). This decides everything else.

## 2. Goal

Establish with real tests whether and how the keys of the Google TV remote can become an input device for VM 170, and produce a motivated recommendation.

Hypothesised architecture (to be verified, not assumed true):

- **Sender on the TV**, two variants to compare:
  - **A, no app:** the VM runs `adb connect` to the TV and reads `adb shell getevent -l`.
  - **B, Android app with an AccessibilityService** that captures the keys and sends them over UDP. For B do analysis and design only: no code, no installation on the TV without the owner's go-ahead (RULES 14, 15).
- **Receiver on VM 170:** a systemd daemon in Python (`python-evdev`) that creates a `uinput` device and re-injects the received keys.
- **Final mapping:** Input Remapper or direct configuration of ES-DE / Kodi.

Out of scope: root, bootloader, flash, system changes to the TV or the router (RULES 1–5).

## 3. What you produce

1. One report per test, in the format of section 5, in `REPORT.md`.
2. `STATUS.md` updated after each test (RULES 38).
3. At the end, a **Recommendation** section: variant A, variant B, neither, or "the sender is not needed". With the data that supports it and what remains unproven.
4. A **Proposals** section with the unrequested ideas (not implemented).

## 4. Tests, in order

Run the tests in order. Do not skip a test and do not move on without the outcome of the previous one. After test 1 and test 5 there is a decision point.

### T1 — How the VM's video reaches the TV
Purpose: identify the method (Moonlight, Steam Link, Kodi, other) and which app is in the foreground on the TV while it shows the VM.
To do: ask the owner how they see the VM on the TV; on the VM list the streaming services and processes read-only (`systemctl list-units --type=service --state=running`, `ps -eo pid,comm,args` filtered by a known name, without `pgrep -f`).
Key question: does the app in the foreground on the TV already receive the remote's keys and forward them to the VM?
Source to look for: the official documentation of the identified app on remote-key support (Android TV / Google TV) and on input forwarding.
**Decision point D1:** if the app already forwards the keys to the VM (proven by the owner pressing keys and watching the effect, and confirmed by `evtest` or `libinput debug-events` on the VM), stop: the sender may not be needed. Report and ask how to proceed. If it does not forward them, or only partly, continue and note which.

### T2 — adb reachable from the VM
Purpose: verify that the VM can reach the TV over `adb`.
Prerequisites: `adb` installed on the VM (check with `command -v adb`; if missing, ask permission, RULES 8). ADB debugging on the TV is enabled **by the owner, by hand**: first explain what it does and how to disable it (RULES 15).
Commands: `timeout 10 adb connect <TV_IP>:5555`, then `timeout 10 adb devices`.
Also verify: the TV asks to authorise the RSA key on the first connection (the owner confirms it on the TV).
Risks to report: ADB key on the TV, TV reachable over the network from the VM, ADB open on the LAN.

### T3 — getevent with the TV on
Purpose: see whether `getevent` reads the remote's events.
Commands, one at a time: `timeout 10 adb shell ls /dev/input`, `timeout 10 adb shell cat /proc/bus/input/devices`, then `timeout 30 adb shell getevent -lt` while the owner presses some keys.
To report: whether the command runs without permission errors, which devices appear, which device is the remote, whether events arrive.
Source to look for: AOSP documentation on `getevent` and the input subsystem; kernel documentation on `evdev`.

### T4 — getevent in standby
Purpose: understand what happens with the TV in standby.
Steps: with the TV in standby (put there by the owner with the remote, not unplugged) try `timeout 10 adb devices`, then `timeout 10 adb connect <TV_IP>:5555`, then `timeout 30 adb shell getevent -lt`.
To report: does the connection survive, drop, reopen by itself? Do keys arrive with the TV in standby? Which key wakes the TV and does that key generate readable events?
Do not switch off or reboot anything via `adb` (RULES 3): standby is triggered by the owner with the remote.

### T5 — Which keys arrive and which are consumed by the UI
Purpose: classify every key of the remote.
Keys to try one at a time, asking the owner to press them and paste the output: arrows, OK/Select, Back, Home, Menu, Assistant/microphone, Play/Pause, Fast forward/Rewind, Volume +/−, Mute, number keys and colour keys if present, app/shortcut keys.
For each key report: does it appear in `getevent`? (yes/no), event name and code, does the TV UI consume it anyway? (yes/no, as observed by the owner), in which screen/app.
Mandatory distinction: `getevent` reads at kernel level (before Android), so it can see keys that the UI then consumes; a key appearing in `getevent` does not mean the VM can stop the TV from reacting to it. Verify it and report it as DONE or HYPOTHESIS.
**Decision point D2:** if Home/Back/Assistant are consumed by the UI and are wanted on the VM side, report the options (ignore them, use other keys, variant B with an AccessibilityService) with cost and risk, and ask how to proceed.

### T6 — adb persistence after reboot
Purpose: know whether `adb` on the TV stays active and reachable after a reboot.
The TV reboot is done by the owner (RULES 3). After the reboot: `timeout 10 adb devices`, `timeout 10 adb connect <TV_IP>:5555`.
To report: is ADB debugging still enabled? Is a new authorisation needed? Does the TV's IP change (DHCP)? What happens after a long standby?
Do not propose router changes to fix the IP: you may only advise the owner to do it themselves (RULES 5).

### T7 — Stream latency
Purpose: measure the video latency VM→TV and understand how much latency the key path adds.
Method: use only tools already present or agreed with the owner. Propose a method (for example an estimate of the interval between the key press and the effect appearing, using the timestamps of `getevent -t` and of `evtest` on the VM), declare it an estimate and report its limits.
To report: stream latency (if measurable), key-path latency (adb getevent → VM), number of measurements, median and maximum. Do not state a value without the measurements.
Source: the documentation of the streaming method identified in T1 on latency.

### T8 — Creating a uinput device on the VM
Purpose: verify that the VM can create a virtual input device.
Read-only checks first: `ls -l /dev/uinput`, `lsmod | grep uinput`, `id`, Python and `python-evdev` versions (`python3 -c 'import evdev; print(evdev.__version__)'`).
Only after the owner's go-ahead (RULES 8, 14, 22): a venv in `~/cec-over-lan/`, a test script that creates a `uinput` device with a few keys, keeps it for a few seconds and sends one key; verify with `evtest` or `libinput debug-events`.
To report: permissions on `/dev/uinput`, whether a udev rule or group membership is needed (state the change and ask permission, do not apply it), name and path of the created device.
Source: the documentation of `python-evdev` and of the kernel on `uinput`.

### T9 — Mapping
Purpose: make the received keys perform the wanted actions in ES-DE / Kodi.
Compare two options and recommend one: (1) Input Remapper; (2) direct mapping in ES-DE / Kodi.
To report: which ES-DE and Kodi actions can be mapped from keyboard/gamepad, how, and with which limits (source: official ES-DE, Kodi and Input Remapper documentation). Try the mapping only after the owner's go-ahead and without changing existing configurations before backing them up (RULES 21).

### T10 — systemd unit with automatic restart
Purpose: run the receiver as a reliable service.
To do: propose the unit text and **do not install it** until the owner approves (RULES 8, 14, 22). Include `Restart=`, `RestartSec=`, a non-root user if possible, network dependencies, logs in `journald`.
Checks after the go-ahead: `systemctl status`, `journalctl -u <unit> -n 50 --no-pager`, kill only the process PID (not `pkill -f`, RULES 12) to verify the automatic restart, reboot the VM only if the owner asks.
To report: restart time, behaviour if the TV is unreachable (`adb` reconnection with backoff), behaviour at boot.

## 5. Report format for each test

Repeat this structure in `REPORT.md` for each test. No section is omitted: if a field does not apply write "not applicable" with the reason.

```
## T<number> — <title>

**Command:** <the exact proposed command>
**What it does (one line):** <explanation>
**Expected output:** <prediction, marked HYPOTHESIS, with the source it comes from>
**Real output:** <output pasted by the owner, copied as is, unmodified>
**Outcome:** <OK | PARTIAL | FAILED | BLOCKED | NOT FEASIBLE>  — <one line of reason>
**Expected/real differences:** <list, or "none">
**Risks:** <risk of the command, risk of the solution, how to undo it>
**DONE / HYPOTHESIS:** <list of claims, each marked, each with a source (RULES 28)>
**I don't know:** <missing data and the command that would provide it>
**Next step:** <a single step>
```

For tests with several commands write a `Command / What it does / Expected output / Real output` block for each command, inside the same section. The outcome covers the whole test.

## 6. How to start

1. Read `RULES.md` and this file. Create `STATUS.md` with "NEXT STEP: T1".
2. Write in one line what you are going to do and ask the owner: **"How do you see VM 170 on the TV (app and method)?"**
3. Wait for the answer. Do not propose anything else.

## 7. Closing

At the end write in `REPORT.md`: a summary table of the tests (T1–T10, outcome), **Recommendation**, **To do** (only what cannot be done here and why), **Proposals**. Then "FINISHED" in `STATUS.md` (RULES 41).
