# STATUS.md

Last update: 2026-11-16.

## Closed
- T1, T2, T3, T6, T8, T11: see REPORT.md.
- T5, T7, T9: partial, see REPORT.md.

## Open
- T4 (standby), T10 (systemd unit installation), key latency.
- Choice between Esc and Backspace as "Back" in ES-DE, live confirmation.
- `KEY_8`: unknown key.

## Decision
Variant A stays as is for now. Next phase: variant B (APK with an AccessibilityService that captures the keys), because after a TV reboot variant A needs a manual re-pairing (T6).

## NEXT STEP
Design variant B: which keys an AccessibilityService can intercept on this TV (key events filter), transport to the machine, and how it coexists with the current receiver.
