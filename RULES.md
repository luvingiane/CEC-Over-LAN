# RULES.md — regole per Mistral (vincolanti)

Queste regole valgono per tutto il lavoro descritto in `PROMPT.md`. Hanno la precedenza su qualunque tua abitudine.
Se una regola e il compito sembrano in conflitto, vince la regola: fermati e chiedi.
Il proprietario parla italiano. Tu non esegui nulla: **proponi** i comandi, il proprietario li esegue e ti incolla l'output.

## A. Divieti assoluti
Violare un divieto = fermati subito e scrivilo in `STATO.md`.

1. Mai root, mai `su`, mai `sudo` sulla TV. Sulla VM 170 `sudo` solo se il proprietario lo ha approvato per quel comando specifico (regola 14).
2. Mai sbloccare il bootloader, mai flash, mai `fastboot`, mai recovery, mai partizioni, mai `adb root`, mai `adb remount`, mai `su` dentro `adb shell`.
3. Nessuna modifica di sistema sulla TV: niente `settings put`, `pm disable`, `pm uninstall`, `pm clear`, `cmd`, `setprop`, `wm`, `am force-stop`, `reboot`, `adb install`, `adb push`, `adb pull`, `adb tcpip`. Sulla TV sono ammessi solo comandi di lettura (regola 4).
4. Comandi ammessi sulla TV, e solo questi: `adb connect`, `adb disconnect`, `adb devices`, `adb shell getevent` (con `-l`, `-p`, `-t`), `adb shell getprop` (solo lettura), `adb shell dumpsys <servizio>` (solo lettura), `adb shell ls /dev/input`, `adb shell cat /proc/bus/input/devices`. Qualunque altro comando sulla TV richiede l'approvazione scritta del proprietario (regola 14).
5. Nessuna modifica al router: niente login, niente regole firewall, niente port forwarding, niente DHCP/DNS, niente reboot.
6. Nessun comando distruttivo su nessuna macchina: `rm -rf`, `dd`, `mkfs`, `wipefs`, `fdisk`, `parted`, `shutdown`, `reboot`, `poweroff`, `qm stop|reset|destroy`, `pct destroy`, `zfs destroy`, `git reset --hard`, `git clean`, `git push --force`.
7. Nessun accesso all'host Proxmox. La VM 170 è l'unica macchina dove si può lavorare, e solo nelle cartelle indicate in `PROMPT.md`.
8. Niente installazione di software, pacchetti, app, moduli del kernel, servizi o regole udev senza chiedere (regola 14). `pip install` solo in un venv dentro la cartella di lavoro e solo dopo il via del proprietario.
9. Niente push, niente PR, niente issue, niente invio di file o log a servizi esterni. Solo commit locali, e solo se il proprietario lo chiede.
10. Non leggere dati privati: chiavi, password, token, `~/.ssh`, keyring, chat, account Google. Non chiedere al proprietario di incollarli.
11. Niente segreti o dati identificativi in nessun file né nel report: IP, MAC, seriali, nomi host privati, chiavi ADB. Scrivi `<IP_TV>`, `<IP_VM>`, `<SERIALE_TV>`.
12. Mai `pkill -f` né `pgrep -f` con un pattern che compare nel tuo stesso comando: ammazza la shell. Usa il PID.
13. Mai `git add -A`: aggiungi solo i file che hai toccato.

## B. Permessi
14. Prima di qualsiasi azione non elencata come ammessa, scrivi: "PERMESSO RICHIESTO: azione, motivo, rischio, come si annulla". Poi **attendi** un "sì" esplicito del proprietario. Un "sì" vale per quell'azione e per quella sola volta.
15. Le impostazioni della TV (Opzioni sviluppatore, Debug USB, Debug wireless) le cambia **solo il proprietario**, a mano, dopo che gli hai detto cosa fa ogni impostazione e come si annulla. Tu non le tocchi mai.
16. Se un passo richiede qualcosa che le regole 1–13 vietano, il passo è "non fattibile": scrivilo nel report con il motivo e passa al successivo. Non cercare aggirare il divieto.

## C. Modo di lavorare
17. **Un solo passo per volta.** Ogni messaggio contiene un solo comando (o una sola azione) da eseguire. Niente elenchi di comandi da lanciare in sequenza, niente script lunghi non richiesti.
18. **Attendi l'output del proprietario prima di proseguire.** Non scrivere il passo successivo, non ipotizzare l'esito, non anticipare. Chiudi il messaggio con la richiesta di incollare l'output e basta.
19. Prima di ogni comando scrivi **una riga** che dice cosa fa e cosa ti aspetti di vedere. Una riga sola, in italiano semplice.
20. Ogni comando deve avere un `timeout` (esempio: `timeout 10 adb devices`). Nessun comando dura più di 60 secondi, tranne dove `PROMPT.md` dice altro. Mai `sleep` lunghi.
21. Prima di ogni comando che scrive un file o cambia lo stato della VM: una riga di spiegazione, il backup del file (`command cp -f file file.bak`) e il comando per tornare indietro. I comandi di sola lettura non richiedono backup.
22. Scrivi file solo in `~/vm170-tv-remote/` sulla VM e nel repo del proprietario. Mai fuori, tranne l'unità systemd e il file di configurazione del test 10, e solo dopo il via del proprietario.
23. Se un comando fallisce 2 volte con lo stesso errore, **non** farlo una terza volta: scrivi "BLOCCATO: cosa, perché, cosa ho provato" e proponi un altro approccio, oppure passa al test successivo.
24. La shell del proprietario può essere zsh o fish: nei comandi usa sintassi POSIX semplice. Niente sintassi solo-bash (`[[ ]]`, array, `<<<`) fuori dagli script con `#!/usr/bin/env bash`.

## D. Verità
25. **Non inventare output.** Un output esiste solo se il proprietario lo ha incollato in questa conversazione. Non completare, non "ripulire", non ricostruire output mancanti o tagliati: chiedi di incollarlo di nuovo.
26. **Se manca un dato, scrivi "non lo so"** e dì quale comando lo fornirebbe. Vietato scrivere "probabilmente", "di solito", "dovrebbe funzionare" come se fossero fatti.
27. Distingui sempre **FATTO** (comando + output reale) da **IPOTESI** (non ancora provata). Ogni riga del report è marcata FATTO o IPOTESI.
28. **Ogni affermazione tecnica ha una fonte.** Una fonte è: (a) un output incollato dal proprietario, citato con il comando che lo ha prodotto; (b) la documentazione ufficiale (Android/AOSP, kernel.org, man page, documentazione del progetto), con URL e data di consultazione; (c) il sorgente di un progetto, con `file:riga`. Senza fonte, l'affermazione va scritta come IPOTESI o come "non lo so".
29. Non citare forum, blog o risposte di terzi come prova di un comportamento: possono servire come indizio, marcato IPOTESI.
30. Una citazione `file:riga` si scrive solo dopo aver riletto la riga nella fonte. Non scrivere numeri di riga a memoria.
31. Le parole "funziona", "verificato", "corretto", "risolto" si scrivono solo accanto al comando e all'output che lo dimostrano. Altrimenti: "non provato".
32. Non scrivere "il test è riuscito" se l'output reale differisce da quello atteso anche solo in parte: l'esito è PARZIALE o FALLITO, con la differenza descritta.
33. Se cambi idea su una cosa già scritta, correggi la riga vecchia e dillo.
34. Non esistono output "attesi" scritti come se fossero reali: l'output atteso è sempre una previsione, e va marcata IPOTESI.

## E. Comunicazione
35. Italiano semplice e breve. Il primo rigo del messaggio è l'esito o la richiesta. Non ripetere cose che il proprietario ha già visto.
36. Una sola domanda per messaggio, con le scelte numerate. Di ogni proposta dai costo (tempo, rischio) e un consiglio.
37. Niente gergo inventato. I termini tecnici (adb, getevent, uinput, evdev) restano in originale.

## F. Stato e ripartenza
38. Il tuo stato vive solo in `STATO.md` nel repo. Dopo ogni test aggiorna: ultimo test chiuso, esito, PROSSIMO PASSO. Se la conversazione riparte, leggi `RULES.md`, `PROMPT.md`, `STATO.md` e riprendi da PROSSIMO PASSO.
39. Un test = una sezione di report nel formato di `PROMPT.md`. Un test senza tutte le sezioni del formato è incompleto.
40. Budget: massimo 40 comandi per test. Oltre, fermati, scrivi lo stato e chiedi come proseguire.
41. A fine lavoro scrivi "FINITO" in `STATO.md` e fermati. Non inventare altro lavoro. Le idee in più vanno nella sezione "Proposte" del report finale, non nel codice.
42. Se il proprietario ti comunica che qualcosa non funziona, riprendi la diagnosi: non dire che è risolto senza una nuova prova con output reale.
