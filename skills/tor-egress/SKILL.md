---
name: tor-egress
description: Instrada ogni richiesta HTTP(S) sul tunnel Tor locale (SOCKS5h, fingerprint TLS da browser reale) così i server remoti vedono l'IP di un exit node invece del nostro. Da usare per QUALSIASI chiamata di rete — probe, GET/POST, payload, login, download, controllo header, loop di polling.
---

# tor-egress — uscita HTTP via Tor (MCP)

**Regola d'oro:** nessuna richiesta di rete esce diretta da questa macchina.
`curl`, `wget`, `python -c requests…`, `sqlmap`, `nikto` lanciati "nudi" lasciano
l'IP reale negli access log del target. Passare **sempre** dai tool MCP qui sotto
(o dagli script in `bin/` se MCP non è disponibile).

Preferisci i tool: `http_request`, `http_batch`, `tor_status`, `tor_exit_ip`,
`tor_newnym`, `tor_selftest`.
Il nome completo dipende dal client: in pi e in Claude Code sono
`mcp__tor_egress__http_request`, `mcp__tor_egress__tor_status`, …
(i `-` del nome server diventano `_`). Se il client non espone l'MCP, usa i CLI
in `bin/` descritti al punto 2.

## 1. Preflight (una volta per sessione)

```
tor_status()
```

- `tunnel_up: false` ⇒ **fermarsi**: avviare Tor (`bash setup-tor.sh` o
  `sudo systemctl start tor`), non procedere "tanto per".
- `exit_ip` deve essere un IP di exit node ≠ IP reale dell'host.
- `check_direct: true` fa **una** uscita diretta verso il servizio di echo per
  confrontare l'IP reale: espone quell'IP a quel solo servizio, usarlo solo se serve.

## 2. Fare le chiamate

| Esigenza | Chiamata |
|---|---|
| singola GET/POST con fingerprint browser | `http_request(url=..., method="GET", headers={...})` |
| body JSON | `http_request(url=..., method="POST", json_body={...})` |
| form-urlencoded | `http_request(url=..., method="POST", data="a=1&b=2")` |
| N richieste / loop / polling | `http_batch(calls=[{...}, {...}], delay=1.3)` |
| scaricare un file / binario | `http_request(url=..., save_path="/tmp/x.bin")` |
| corpo grande troncato | `max_body_chars=50000` (il resto va in `full_body_file`) |
| certificato self-signed | `verify_tls=false` (equivale a `curl -k`) |
| nuovo exit node | `tor_newnym(verify=true)` |

Comandi atomici, se l'MCP non è caricato:

```bash
python3 bin/torcurl.py GET https://target/path -H 'K: V' -q      # body puro
python3 bin/torcurl.py POST https://target/api --json '{"a":1}'
python3 bin/torcurl.py GET https://target/path -o /tmp/out.html   # salva
bin/acurl -sk 'https://target/path'                              # curl vero, forzato su Tor
```

## 3. Vincoli tecnici da rispettare

- **Codificare l'URL**: payload con spazi/apici/parentesi vanno percent-encoded,
  altrimenti la richiesta parte monca.
  `python3 -c "import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1],safe=''))" 'x OR 1=1-- '`
- **Passo**: il client applica già `TOR_MIN_INTERVAL` (default 1,2 s) fra richieste
  consecutive; non abbassarlo "per finire prima" e non lanciare batch paralleli.
- **DNS**: solo `socks5h` (risolto dall'exit node). `socks5` senza `h` = risoluzione
  locale = leak. Il client forza già `socks5h`.
- **429**: `http_request` attende e riprova da solo (fino a 4 tentativi). Se persiste:
  `tor_newnym()`, poi riprova più lentamente.
- **TLS**: il certificato del target è quasi sempre valido; `verify_tls=false` è
  tollerato ma non necessario.

## 4. Ridurre l'impronta comportamentale

Tor nasconde il **chi**, non il **come**. Quando ha senso somigliare al traffico
reale dell'applicazione, aggiungi gli header che quella UI manda:

```python
http_request(url=..., headers={
  "X-Requested-With": "XMLHttpRequest",
  "Referer": "https://target/pagina-che-chiama-l-endpoint",
  "Accept": "*/*",
  "Accept-Language": "it-IT,it;q=0.9",
})
```

`impersonate` (default `chrome`) allinea già JA3/JA4 e l'ordine degli header HTTP/2.
Altri valori: `firefox`, `safari`, `edge`, `chrome110`, `firefox120`, …

## 5. Ruotare l'identità

`tor_newnym(verify=true)` → nuovo circuito + conferma dell'exit node. Quando:
dopo un 429 ostinato, se l'exit node è bloccato/403, o all'inizio di una fase nuova.
Il rate limiter di molti target conta per IP ⇒ ogni rotazione resetta il budget.

## 6. Igiene locale (le tracce restano anche qui)

- Non salvare risposte con dati sensibili (password, anagrafiche) se non servono;
  se servono, scrivile in una cartella di lavoro e cancellale a fine sessione.
- I payload finiscono **nell'URL**: restano nei log di shell e in quelli del target.
  Prima di incollare output in un report, verifica che non contengano credenziali.
- Non mettere cookie/token nei file di config dell'MCP: passali per chiamata.

## 7. Cosa NON fare

- Niente port-scan o scan aggressivi via Tor: lenti, inaffidabili e rumorosi.
- Niente raffiche "giusto per sbrigare": il rumore lo fa il pattern, non il colpo.
- Non disattivare il guard per "provare se funziona": se `tunnel_up` è false, si ripara.
- Non usare `allow_direct=true` (e non impostare `TOR_ALLOW_DIRECT=1`) se non per una
  verifica esplicita e dichiarata all'utente.

## 8. Verifica rapida dopo qualsiasi modifica

```
http_batch(calls=[{"url": "https://checkip.amazonaws.com", "max_body_chars": 60}] * 3, delay=1.0)
```

Tre IP coerenti fra loro e diversi dal nostro IP = tutto a posto. Se uno è assente o
è l'IP reale, indagare prima di proseguire. `tor_selftest()` fa la stessa cosa in un colpo.
