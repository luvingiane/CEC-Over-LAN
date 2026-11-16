# STATO.md

Ultimo aggiornamento: 2026-11-16.

## Chiusi
- T1, T2, T3, T8: vedi REPORT.md.
- T5, T7, T9: parziali, vedi REPORT.md.

## Aperti
- T4 (standby), T6 (persistenza adb dopo reboot, stabilità della porta), T10 (unità systemd), latenza dei tasti.
- Scelta tra Esc e Backspace come "Indietro" in ES-DE, conferma live.
- Azione del tasto Giallo (`KEY_SHUFFLE`) e identificazione di `KEY_8`.

## PROSSIMO PASSO
T6: il proprietario riavvia la TV, poi `adb connect` sulla VM per vedere se la porta del debug wireless resta la stessa.
