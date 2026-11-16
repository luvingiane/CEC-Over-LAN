# PROMPT.md — Analisi e test di fattibilità: telecomando Google TV come input della VM 170

Leggi prima `RULES.md`. Le regole lì dentro sono vincolanti e prevalgono su questo documento.
Non eseguire nulla da solo: proponi un passo per volta, il proprietario lo esegue e ti incolla l'output.

## 1. Contesto

- La **VM 170** è una VM Debian su Proxmox: box gaming/multimediale. La sua uscita video è mostrata sulla **TV Google TV** del proprietario.
- Il **telecomando fisico della Google TV** oggi controlla la TV, non la VM.
- Non è ancora noto **come il video della VM arriva alla TV** (Moonlight, Steam Link, Kodi, altro). Questo dato decide tutto il resto.

## 2. Obiettivo

Stabilire con prove reali se e come i tasti del telecomando della Google TV possono diventare un dispositivo di input della VM 170, e produrre una raccomandazione motivata.

Architettura ipotizzata (da verificare, non da dare per vera):

- **Sender sulla TV**, due varianti da confrontare:
  - **A, senza app:** la VM fa `adb connect` alla TV e legge `adb shell getevent -l`.
  - **B, app Android con AccessibilityService** che cattura i tasti e li invia via UDP. Per B fai solo analisi e design: niente codice, niente installazione sulla TV senza il via del proprietario (RULES 14, 15).
- **Receiver sulla VM 170:** demone systemd in Python (`python-evdev`) che crea un device `uinput` e re-inietta i tasti ricevuti.
- **Mappatura finale:** Input Remapper oppure configurazione diretta di ES-DE / Kodi.

Fuori da questo compito: root, bootloader, flash, modifiche di sistema alla TV o al router (RULES 1–5).

## 3. Cosa produci

1. Un report per ogni test, nel formato della sezione 5, in `REPORT.md`.
2. `STATO.md` aggiornato dopo ogni test (RULES 38).
3. Alla fine, una sezione **Raccomandazione**: variante A, variante B, nessuna delle due, oppure "il sender non serve". Con i dati che la sostengono e quello che resta non provato.
4. Una sezione **Proposte** con le idee non richieste (non realizzate).

## 4. Test, in ordine

Esegui i test nell'ordine. Non saltare un test e non passare al successivo senza l'esito del precedente. Dopo il test 1 e il test 5 c'è un punto di decisione.

### T1 — Come arriva il video della VM alla TV
Scopo: identificare il metodo (Moonlight, Steam Link, Kodi, altro) e quale app è in primo piano sulla TV mentre mostra la VM.
Da fare: chiedi al proprietario come vede la VM sulla TV; sulla VM elenca i servizi e i processi di streaming in sola lettura (`systemctl list-units --type=service --state=running`, `ps -eo pid,comm,args` filtrato per nome noto, senza `pgrep -f`).
Domanda chiave: l'app in primo piano sulla TV riceve già i tasti del telecomando e li inoltra alla VM?
Fonte da cercare: documentazione ufficiale dell'app individuata sul supporto dei tasti del telecomando (Android TV / Google TV) e sull'inoltro dell'input.
**Punto di decisione D1:** se l'app inoltra già i tasti alla VM (provato dal proprietario premendo i tasti e osservando l'effetto, e confermato da `evtest` o `libinput debug-events` sulla VM), fermati: il sender potrebbe non servire. Riporta e chiedi come proseguire. Se non li inoltra, o li inoltra solo in parte, continua e annota quali.

### T2 — adb raggiungibile dalla VM
Scopo: verificare che la VM raggiunga la TV via `adb`.
Prerequisiti: `adb` installato sulla VM (verifica con `command -v adb`; se manca, chiedi permesso, RULES 8). Il debug ADB sulla TV è abilitato **dal proprietario a mano**: spiegagli cosa fa e come si disabilita prima (RULES 15).
Comandi: `timeout 10 adb connect <IP_TV>:5555`, poi `timeout 10 adb devices`.
Verifica anche: la TV chiede l'autorizzazione della chiave RSA al primo collegamento (è il proprietario a confermarla sul televisore).
Rischi da riportare: chiave ADB sulla TV, TV raggiungibile via rete dalla VM, ADB aperto sulla LAN.

### T3 — getevent con TV accesa
Scopo: vedere se `getevent` legge gli eventi del telecomando.
Comandi, uno per volta: `timeout 10 adb shell ls /dev/input`, `timeout 10 adb shell cat /proc/bus/input/devices`, poi `timeout 30 adb shell getevent -lt` mentre il proprietario preme alcuni tasti.
Da riportare: se il comando gira senza errori di permessi, quali device compaiono, quale device corrisponde al telecomando, se gli eventi arrivano.
Fonte da cercare: documentazione AOSP su `getevent` e sul sottosistema input; documentazione del kernel su `evdev`.

### T4 — getevent in standby
Scopo: capire cosa succede con la TV in standby.
Passi: con TV in standby (messa in standby dal proprietario con il telecomando, non scollegata) prova `timeout 10 adb devices`, poi `timeout 10 adb connect <IP_TV>:5555`, poi `timeout 30 adb shell getevent -lt`.
Da riportare: la connessione sopravvive, cade, si riapre da sola? Con la TV in standby i tasti arrivano? Quale tasto sveglia la TV e quel tasto genera eventi leggibili?
Non spegnere né riavviare nulla via `adb` (RULES 3): lo standby lo attiva il proprietario con il telecomando.

### T5 — Quali tasti arrivano e quali li consuma la UI
Scopo: classificare ogni tasto del telecomando.
Tasti da provare uno alla volta, chiedendo al proprietario di premerli e incollare l'output: frecce, OK/Select, Back, Home, Menu, Assistant/microfono, Play/Pausa, Avanti/Indietro veloce, Volume +/−, Mute, tasti numerici e tasti colorati se presenti, tasti app/scorciatoie.
Per ogni tasto riporta: appare in `getevent`? (sì/no), nome e codice evento, la UI della TV lo consuma comunque? (sì/no, come osservato dal proprietario), in quale schermata/app.
Distinzione obbligatoria: `getevent` legge a livello kernel (prima di Android), quindi può vedere tasti che la UI poi consuma; il fatto che un tasto compaia in `getevent` non dice che la VM possa impedire alla TV di reagire. Verificalo e riportalo come FATTO o IPOTESI.
**Punto di decisione D2:** se Home/Back/Assistant sono consumati dalla UI e si vogliono usare lato VM, riporta le opzioni (ignorarli, usare altri tasti, variante B con AccessibilityService) con costo e rischio, e chiedi come proseguire.

### T6 — Persistenza di adb dopo reboot
Scopo: sapere se `adb` sulla TV resta attivo e raggiungibile dopo un riavvio.
Il riavvio della TV lo fa il proprietario (RULES 3). Dopo il riavvio: `timeout 10 adb devices`, `timeout 10 adb connect <IP_TV>:5555`.
Da riportare: il debug ADB è ancora abilitato? Serve una nuova autorizzazione? L'IP della TV cambia (DHCP)? Cosa succede dopo lo standby prolungato?
Non proporre modifiche al router per fissare l'IP: puoi solo consigliare al proprietario di farlo lui (RULES 5).

### T7 — Latenza dello stream
Scopo: misurare la latenza del video VM→TV e capire quanta latenza aggiunge il percorso dei tasti.
Metodo: usa solo strumenti già presenti o concordati con il proprietario. Proponi un metodo (per esempio stima dell'intervallo tra pressione del tasto e comparsa dell'effetto, con i timestamp di `getevent -t` e di `evtest` sulla VM), dichiaralo come stima e riporta i limiti.
Da riportare: latenza dello stream (se misurabile), latenza del percorso tasti (adb getevent → VM), numero di misure, mediana e massimo. Non dichiarare un valore senza le misure.
Fonte: documentazione del metodo di streaming individuato in T1 sulla latenza.

### T8 — Creazione di un device uinput sulla VM
Scopo: verificare che la VM possa creare un device di input virtuale.
Verifiche in sola lettura per prime: `ls -l /dev/uinput`, `lsmod | grep uinput`, `id`, versione di Python e di `python-evdev` (`python3 -c 'import evdev; print(evdev.__version__)'`).
Solo dopo il via del proprietario (RULES 8, 14, 22): venv in `~/vm170-tv-remote/`, script di prova che crea un device `uinput` con pochi tasti, lo mantiene per pochi secondi e invia un tasto; verifica con `evtest` o `libinput debug-events`.
Da riportare: permessi su `/dev/uinput`, se serve una regola udev o l'appartenenza a un gruppo (indica la modifica e chiedi il permesso, non applicarla), nome e percorso del device creato.
Fonte: documentazione di `python-evdev` e del kernel su `uinput`.

### T9 — Mappatura
Scopo: far fare ai tasti ricevuti le azioni volute in ES-DE / Kodi.
Confronta due opzioni e raccomanda una: (1) Input Remapper; (2) mappatura diretta in ES-DE / Kodi.
Da riportare: quali azioni di ES-DE e Kodi si mappano da tastiera/gamepad, come, e con quali limiti (fonte: documentazione ufficiale di ES-DE, Kodi, Input Remapper). Prova la mappatura solo dopo il via del proprietario e senza modificare configurazioni esistenti prima di averne fatto il backup (RULES 21).

### T10 — Unità systemd con restart automatico
Scopo: far girare il receiver come servizio affidabile.
Da fare: proponi il testo dell'unità e **non installarla** finché il proprietario non approva (RULES 8, 14, 22). Includi `Restart=`, `RestartSec=`, utente non root se possibile, dipendenze di rete, log in `journald`.
Verifiche dopo il via: `systemctl status`, `journalctl -u <unità> -n 50 --no-pager`, kill del solo PID del processo (non `pkill -f`, RULES 12) per verificare il riavvio automatico, riavvio della VM solo se il proprietario lo chiede.
Da riportare: tempo di riavvio, comportamento se la TV non è raggiungibile (riconnessione `adb` con backoff), comportamento al boot.

## 5. Formato del report per ogni test

Ripeti questa struttura in `REPORT.md` per ogni test. Nessuna sezione si omette: se un campo non è applicabile scrivi "non applicabile" con il motivo.

```
## T<numero> — <titolo>

**Comando:** <il comando esatto proposto>
**Cosa fa (una riga):** <spiegazione>
**Output atteso:** <previsione, marcata IPOTESI, con la fonte da cui nasce>
**Output reale:** <output incollato dal proprietario, copiato così com'è, senza modifiche>
**Esito:** <OK | PARZIALE | FALLITO | BLOCCATO | NON FATTIBILE>  — <una riga di motivo>
**Differenze atteso/reale:** <elenco, o "nessuna">
**Rischi:** <rischio del comando, rischio della soluzione, come si annulla>
**FATTO / IPOTESI:** <elenco di affermazioni, ognuna marcata, ognuna con fonte (RULES 28)>
**Non lo so:** <dati mancanti e comando che li fornirebbe>
**Prossimo passo:** <un solo passo>
```

Per i test con più comandi scrivi un blocco `Comando / Cosa fa / Output atteso / Output reale` per ogni comando, dentro la stessa sezione. L'esito vale per l'intero test.

## 6. Come cominci

1. Leggi `RULES.md` e questo file. Crea `STATO.md` con "PROSSIMO PASSO: T1".
2. Scrivi in una riga cosa farai e chiedi al proprietario: **"Come vedi la VM 170 sulla TV (app e metodo)?"**
3. Aspetta la risposta. Non proporre altro.

## 7. Chiusura

Alla fine scrivi in `REPORT.md`: tabella riassuntiva dei test (T1–T10, esito), **Raccomandazione**, **Da fare** (solo ciò che non si può fare qui e perché), **Proposte**. Poi "FINITO" in `STATO.md` (RULES 41).
