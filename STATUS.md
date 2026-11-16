# STATUS.md

Last update: 2026-11-16.

## Closed
- T1, T2, T3, T8, T11: see REPORT.md.
- T5, T7, T9: partial, see REPORT.md.

## Open
- T4 (standby), T6 (adb persistence after reboot: the old port was `offline` after a TV reboot, new port still to be tested), T10 (systemd unit), key latency.
- Choice between Esc and Backspace as "Back" in ES-DE, live confirmation.
- `KEY_8`: unknown key.

## NEXT STEP
T6: read the new wireless debugging port from the TV (Developer options -> Wireless debugging), `adb connect` from the machine, and check whether pairing is still valid.
