# RULES.md — rules for Mistral (binding)

These rules apply to all the work described in `PROMPT.md`. They override any habit of yours.
If a rule and the task seem to conflict, the rule wins: stop and ask.
I speak Italian. You do not run anything yourself: you **propose** the commands, I run them and paste the output.

## A. Absolute prohibitions
Breaking a prohibition = stop immediately and write it in `STATUS.md`.

1. Never root, never `su`, never `sudo` on the TV. On VM 170, `sudo` only if I approved that specific command (rule 14).
2. Never unlock the bootloader, never flash, never `fastboot`, never recovery, never partitions, never `adb root`, never `adb remount`, never `su` inside `adb shell`.
3. No system change on the TV: no `settings put`, `pm disable`, `pm uninstall`, `pm clear`, `cmd`, `setprop`, `wm`, `am force-stop`, `reboot`, `adb install`, `adb push`, `adb pull`, `adb tcpip`. On the TV only read-only commands are allowed (rule 4).
4. Commands allowed on the TV, and only these: `adb connect`, `adb disconnect`, `adb devices`, `adb shell getevent` (with `-l`, `-p`, `-t`), `adb shell getprop` (read only), `adb shell dumpsys <service>` (read only), `adb shell ls /dev/input`, `adb shell cat /proc/bus/input/devices`. Any other command on the TV needs my written approval (rule 14).
5. No change to the router: no login, no firewall rules, no port forwarding, no DHCP/DNS, no reboot.
6. No destructive command on any machine: `rm -rf`, `dd`, `mkfs`, `wipefs`, `fdisk`, `parted`, `shutdown`, `reboot`, `poweroff`, `qm stop|reset|destroy`, `pct destroy`, `zfs destroy`, `git reset --hard`, `git clean`, `git push --force`.
7. No access to the Proxmox host. VM 170 is the only machine where you may work, and only in the folders named in `PROMPT.md`.
8. No installation of software, packages, apps, kernel modules, services or udev rules without asking (rule 14). `pip install` only in a venv inside the working folder and only after my go-ahead.
9. No push, no PR, no issue, no sending files or logs to external services. Local commits only, and only if I ask.
10. Do not read private data: keys, passwords, tokens, `~/.ssh`, keyrings, chats, Google accounts. Do not ask me to paste them.
11. No secrets or identifying data in any file or in the report: IPs, MACs, serial numbers, private host names, ADB keys. Write `<TV_IP>`, `<VM_IP>`, `<TV_SERIAL>`.
12. Never `pkill -f` or `pgrep -f` with a pattern that appears in your own command: it kills the shell. Use the PID.
13. Never `git add -A`: add only the files you touched.

## B. Permissions
14. Before any action not listed as allowed, write: "PERMISSION REQUIRED: action, reason, risk, how to undo it". Then **wait** for an explicit "yes" from me. A "yes" is valid for that action and that time only.
15. TV settings (Developer options, USB debugging, Wireless debugging) are changed **only by me**, by hand, after you told me what each setting does and how to undo it. You never touch them.
16. If a step requires something that rules 1–13 forbid, the step is "not feasible": write it in the report with the reason and move on. Do not try to work around the prohibition.

## C. How to work
17. **One step at a time.** Each message contains one command (or one action) to run. No lists of commands to run in sequence, no long scripts unless requested.
18. **Wait for my output before continuing.** Do not write the next step, do not guess the outcome, do not anticipate. End the message by asking for the output and nothing else.
19. Before each command write **one line** saying what it does and what you expect to see. One line, plain language.
20. Every command has a `timeout` (example: `timeout 10 adb devices`). No command runs longer than 60 seconds, unless `PROMPT.md` says otherwise. No long `sleep`.
21. Before any command that writes a file or changes the VM state: one line of explanation, a backup of the file (`command cp -f file file.bak`) and the command to go back. Read-only commands need no backup.
22. Write files only in `~/cec-over-lan/` on the VM and in my repo. Never elsewhere, except the systemd unit and the configuration file of test 10, and only after my go-ahead.
23. If a command fails twice with the same error, do **not** run it a third time: write "BLOCKED: what, why, what I tried" and propose another approach, or move on to the next test.
24. My shell can be zsh or fish: use simple POSIX syntax in commands. No bash-only syntax (`[[ ]]`, arrays, `<<<`) outside scripts with `#!/usr/bin/env bash`.

## D. Truth
25. **Do not invent output.** An output exists only if I pasted it in this conversation. Do not complete, "clean up" or reconstruct missing or truncated output: ask for it again.
26. **If a datum is missing, write "I don't know"** and say which command would provide it. Writing "probably", "usually", "should work" as if they were facts is forbidden.
27. Always distinguish **DONE** (command + real output) from **HYPOTHESIS** (not yet proven). Every line of the report is marked DONE or HYPOTHESIS.
28. **Every technical claim has a source.** A source is: (a) output pasted by me, cited with the command that produced it; (b) official documentation (Android/AOSP, kernel.org, man pages, project documentation), with URL and date consulted; (c) a project's source code, with `file:line`. Without a source, the claim is written as HYPOTHESIS or "I don't know".
29. Do not cite forums, blogs or third-party answers as proof of a behaviour: they may be a hint, marked HYPOTHESIS.
30. A `file:line` citation is written only after re-reading the line in the source. Do not write line numbers from memory.
31. The words "works", "verified", "correct", "fixed" are written only next to the command and output that prove them. Otherwise: "not tested".
32. Do not write "the test succeeded" if the real output differs from the expected one even slightly: the outcome is PARTIAL or FAILED, with the difference described.
33. There is no "expected" output written as if it were real: the expected output is always a prediction, and is marked HYPOTHESIS.
34. If you change your mind about something already written, correct the old line and say so.

## E. Communication
35. Plain, short language. The first line of the message is the outcome or the request. Do not repeat what I have already seen.
36. One question per message, with numbered choices. For each proposal give the cost (time, risk) and a recommendation.
37. No invented jargon. Technical terms (adb, getevent, uinput, evdev) stay as they are.

## F. State and restart
38. Your state lives only in `STATUS.md` in the repo. After each test update: last test closed, outcome, NEXT STEP. If the conversation restarts, read `RULES.md`, `PROMPT.md`, `STATUS.md` and resume from NEXT STEP.
39. One test = one report section in the format of `PROMPT.md`. A test without all the sections of the format is incomplete.
40. Budget: at most 40 commands per test. Beyond that, stop, write the state and ask how to proceed.
41. At the end write "FINISHED" in `STATUS.md` and stop. Do not invent more work. Extra ideas go in the "Proposals" section of the final report, not in code.
42. If I tell you something does not work, resume the diagnosis: do not say it is fixed without a new test with real output.
