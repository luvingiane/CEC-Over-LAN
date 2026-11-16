# REPORT.md — telecomando Google TV come input della VM 170

Data: 2026-11-16. TV: modello `MiTV_MZTU0` (codename `river`). Indirizzi: `<IP_TV>`, `<IP_VM>`.
Marcatura: FATTO = comando + output reale; IPOTESI = non provato.

| Test | Esito |
|---|---|
| T1 Streaming video | OK |
| T2 adb dalla VM | OK |
| T3 getevent, TV accesa | OK |
| T4 getevent, standby | NON PROVATO |
| T5 Tasti e UI | PARZIALE |
| T6 Persistenza dopo reboot | NON PROVATO |
| T7 Latenza | PARZIALE |
| T8 Device uinput | OK |
| T9 Mappatura | PARZIALE |
| T10 Unità systemd | PREPARATA, NON INSTALLATA |

## T1 — Come arriva il video della VM alla TV
**Comando:** domanda al proprietario.
**Output reale:** "HDMI da 20mt in FO" (cavo HDMI in fibra ottica, 20 m).
**Esito:** OK. FATTO: il video arriva in HDMI diretto, senza streaming. L'app in primo piano è l'ingresso HDMI, quindi non c'è un'app che inoltri i tasti alla VM: **il sender serve** (D1 chiuso).
**IPOTESI:** HDMI-CEC come via di inoltro dei tasti non è stato verificato: dipende dalla scheda video della VM e dal cavo in fibra.
**Rischi:** nessuno.

## T2 — adb raggiungibile dalla VM
**Comando:** `adb pair <IP_TV>:<porta_pairing> <codice>`, `adb connect <IP_TV>:<porta>`, `adb devices -l` (eseguiti sulla VM).
**Output reale:** `Successfully paired to ...`, `connected to ...`, `device product:river model:MiTV_MZTU0 device:river`.
**Esito:** OK. FATTO.
**Rischi:** la VM ora ha una chiave ADB autorizzata sulla TV. Si revoca dalle Opzioni sviluppatore della TV ("Revoca autorizzazioni debug"). Il debug wireless è raggiungibile dalla LAN finché è attivo.

## T3 — getevent con TV accesa
**Comandi:** `adb shell ls -l /dev/input`, `adb shell cat /proc/bus/input/devices`, `adb shell getevent -p`, `adb shell getevent -lt` (20 s).
**Output reale:** 5 device (`event0`–`event4`); `/proc/bus/input/devices` negato all'utente `shell` ("Permission denied"); `getevent -p` mostra i nomi: "Xiaomi RC Keyboard" (event4), "Xiaomi RC Consumer Control" (event3), "MTK PMU WOBT", "MTK Smart TV IR Receiver", "MTK TV KEYPAD". Gli eventi dei tasti sono arrivati tutti da `event3`.
**Esito:** OK. FATTO.
**Nota:** questa versione di `getevent` accetta un solo device alla volta; senza argomento li legge tutti.
**Rischi:** solo lettura.

## T4 — getevent in standby
**Esito:** NON PROVATO. Non so se `adb` resta raggiungibile e se i tasti arrivano con la TV in standby. Da fare: il proprietario mette la TV in standby, poi `adb devices`, `adb connect`, `getevent -lt`.

## T5 — Tasti e consumo da parte della UI
**FATTO (dai log):** codici visti: `KEY_UP/DOWN/LEFT/RIGHT`, `KEY_SELECT`, `KEY_BACK`, `KEY_VOLUMEUP/DOWN`, `KEY_CHANNELUP/DOWN`, `KEY_SEARCH`, `KEY_STOPCD`, `KEY_EJECTCD`, `KEY_SHUFFLE`, `KEY_8`. Ogni pressione genera `DOWN` e `UP`.
**FATTO (conferma del proprietario e prove):** tasto Verde tenuto 3 s -> combo `Alt+F4` in dry-run; tocco breve -> nessun effetto.
**FATTO (prova del proprietario):** Rosso = `KEY_STOPCD`, Verde = `KEY_EJECTCD`, Giallo = `KEY_SHUFFLE` (il Giallo apre la panoramica di GNOME), Blu = `KEY_SEARCH`. `KEY_8` non è associato a un tasto noto.
**Non lo so:** Home, Play/Pausa, Mute e Assistant fisici non sono comparsi nei log; non so se li consuma la TV prima del kernel. `getevent` legge a livello kernel, quindi non dimostra che la TV non reagisca allo stesso tasto.
**Esito:** PARZIALE.

## T6 — Persistenza di adb dopo reboot
**Esito:** NON PROVATO. Da verificare: debug wireless ancora attivo, nuova autorizzazione richiesta, **la porta di connessione cambia?** (determina se un servizio può usare una porta fissa).

## T7 — Latenza
**FATTO:** non c'è un flusso video da misurare (HDMI diretto). **Non misurata** la latenza del percorso tasti (TV -> adb -> VM -> uinput). Il proprietario riferisce che i tasti funzionano; non c'è un numero.
**Esito:** PARZIALE.

## T8 — Device uinput
**Comandi (sulla VM):** `ls -l /dev/uinput`, `getfacl /dev/uinput`, `python3 -c "import evdev"`, `grep -A4 "TV Remote" /proc/bus/input/devices`.
**Output reale:** `/dev/uinput` è `root:root` con ACL `user:claude:rw-`; `python-evdev` importabile; device "TV Remote (vm170)" creato su `event19` come tastiera.
**Esito:** OK. FATTO. Nessuna modifica di permessi né installazione necessaria.
**Rischi:** il device inietta tasti reali sulla VM (GNOME ed ES-DE li ricevono).

## T9 — Mappatura
**Scelta del proprietario (FATTO):** frecce -> frecce; OK -> Invio; Indietro -> Backspace; Canale su/giù -> media avanti/indietro (`KEY_NEXTSONG`/`KEY_PREVIOUSSONG`); `KEY_SEARCH` -> Play/Pausa; `KEY_STOPCD` -> Home (`KEY_HOMEPAGE`); `KEY_EJECTCD` -> `Alt+F4` dopo 3 s; `KEY_SHUFFLE` -> `KEY_LEFTMETA` (tocco = panoramica GNOME).
**FATTO (VM, GNOME Shell 48.7):** `gsettings get org.gnome.mutter overlay-key` -> `'Super'`; `switch-applications` -> `['<Super>Tab', '<Alt>Tab']`. Alt+Tab da un solo tasto alternerebbe solo le ultime due app, quindi si è scelta la panoramica (frecce + Invio per scegliere). Il proprietario conferma che funziona.
**FATTO (fonte `USERGUIDE.md` di ES-DE):** `USERGUIDE.md:651` frecce, `:661-663` Esc = menu principale (non Indietro), `:686` Invio = A, `:691` "Back key" = B (non nomina Backspace), `:705` `Alt+F4` esce.
**FATTO (prova del proprietario):** ES-DE reagisce ai tasti iniettati ("funziona").
**IPOTESI:** Backspace come tasto B in ES-DE: da confermare. WASD non funziona in ES-DE di default (non citato nella guida); scartato a favore delle frecce.
**Non fatto:** Kodi (profilo assente), Input Remapper non provato.
**Esito:** PARZIALE.

## T10 — Unità systemd
**Fatto:** `receiver/tv-remote.service` (unità utente, `Restart=always`, `RestartSec=2`). **Non installata come file.**
**FATTO:** il receiver gira come servizio utente transitorio (`systemd-run --user --unit=tv-remote -p Restart=always -p RestartSec=2 ...`), `systemctl --user is-active tv-remote` -> `active`, device `TV Remote (vm170)` creato. Non sopravvive a un riavvio della VM.
**Non lo so:** comportamento al boot, se la TV non risponde, tempo di riavvio dopo kill del processo.
**Rischi:** la porta del debug wireless può cambiare (vedi T6). Senza `loginctl enable-linger` il servizio parte solo con il login.

## Raccomandazione (provvisoria)
Variante A (senza app sulla TV) funziona. Prima di renderla permanente: T6 (stabilità della porta) e T4 (standby). Se la porta cambia a ogni riavvio, la variante A richiede un nuovo pairing manuale e la variante B diventa preferibile.

## Da fare
T4, T6, T10, misura della latenza, conferma di Backspace in ES-DE, Kodi, azione per `KEY_SHUFFLE`.

## Proposte
- Pressione lunga anche per altri tasti.
- Riconnessione automatica con ricerca della porta via mDNS (`adb mdns services`), da verificare.
