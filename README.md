# vm170-tv-remote

Usa il telecomando fisico di una Google TV come dispositivo di input di una VM Linux collegata alla TV via HDMI.

Come funziona (variante A, nessuna app sulla TV):

1. La VM si collega alla TV con `adb` (debug wireless, pairing una tantum).
2. `receiver/tv_remote.py` legge `adb shell getevent -l`, traduce i tasti e li re-inietta in un device `uinput` ("TV Remote (vm170)").
3. La VM vede una normale tastiera: ES-DE, Kodi e GNOME reagiscono.

Provato su: Debian 13 con GNOME Shell 48.7, TV `MiTV_MZTU0` (Google TV), cavo HDMI in fibra. Il report completo dei test è in [REPORT.md](REPORT.md).

## Mappatura predefinita

| Telecomando | Evento letto | La VM inietta |
|---|---|---|
| Frecce | `KEY_UP/DOWN/LEFT/RIGHT` | frecce |
| OK | `KEY_SELECT` | Invio |
| Indietro | `KEY_BACK` | Backspace |
| Canale su / giù | `KEY_CHANNELUP/DOWN` | media avanti / indietro |
| Rosso | `KEY_STOPCD` | Home (`KEY_HOMEPAGE`) |
| Verde (3 s) | `KEY_EJECTCD` | Alt+F4 |
| Giallo | `KEY_SHUFFLE` | Super (panoramica GNOME) |
| Blu | `KEY_SEARCH` | Play/Pausa |

Il volume resta alla TV. I tasti Home, Mute e Assistant del telecomando non arrivano a `getevent`.

## Requisiti

- Sulla TV: Opzioni sviluppatore e Debug wireless attivi, abilitati a mano dal proprietario.
- Sulla VM: `adb` (`android-tools-adb`), Python 3 con `python-evdev`, accesso in scrittura a `/dev/uinput` (qui via ACL).

## Uso

```bash
adb pair <IP_TV>:<PORTA_PAIRING> <CODICE>      # una volta sola
adb connect <IP_TV>:<PORTA>
python3 receiver/tv_remote.py --target <IP_TV>:<PORTA> --dry-run   # stampa e basta
python3 receiver/tv_remote.py --target <IP_TV>:<PORTA>             # inietta davvero
```

Come servizio: vedi `receiver/tv-remote.service` (unità utente, `Restart=always`).

## Limiti noti

- La porta del Debug wireless può cambiare: dopo un riavvio della TV può servire un nuovo `adb connect`. Non verificato, vedi T6 in REPORT.md.
- Comportamento con la TV in standby non verificato (T4).
- Latenza dei tasti non misurata.

## Altri file

- `RULES.md`, `PROMPT.md`: regole e compito dati all'agente che ha condotto i test.
- `STATO.md`: stato dei test.
