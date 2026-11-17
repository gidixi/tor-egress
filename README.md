# tor-egress

MCP server (stdio) + skill per **instradare ogni richiesta HTTP(S) su Tor**, con
fingerprint TLS/HTTP2 da browser reale. Autonomo e senza path cablati: si copia in
qualsiasi progetto, su qualsiasi macchina.

Il problema che risolve: un agente che usa `curl`, `wget` o `requests` in modo
diretto mette l'IP della macchina negli access log del target, in chiaro. Qui l'IP
che il target vede è quello di un exit node Tor, e la richiesta ha la firma di
Chrome/Firefox, non di un tool di scan.

```
agent ──tool MCP──▶ server.py ──socks5h──▶ Tor (127.0.0.1:9050) ──▶ target
                        │
                        └─ guard: tunnel giù ⇒ la richiesta NON parte
```

## Cosa c'è dentro

```
tor-egress/
├── extension.js            estensione pi: registra l'MCP server (nessuna dipendenza npm)
├── package.json            manifest pi (extension + skills) e scripts di test
├── server/
│   ├── server.py           MCP server stdio (FastMCP): 6 tool
│   └── torhttp.py          nucleo: tunnel, sessione, passo minimo, retry, NEWNYM
├── skills/tor-egress/      SKILL.md: istruzioni d'uso per l'agente
├── bin/
│   ├── torcurl.py          CLI equivalente ai tool (fallback senza MCP)
│   └── acurl               curl forzato su Tor (si ferma se il tunnel è giù)
├── install.sh              installer da URL raw (curl | bash)
├── setup-tor.sh            installa/configura Tor + ControlPort + dipendenze Python
├── selftest.py             handshake MCP + richieste reali via Tor
└── requirements.txt        mcp, curl_cffi
```

## Tool esposti

| Tool | A cosa serve |
|---|---|
| `tor_status(check_direct=false)` | tunnel SOCKS, ControlPort, exit node corrente, leak check |
| `tor_exit_ip()` | solo l'IP con cui ci vede il mondo |
| `tor_newnym(verify=true, wait=4)` | nuovo circuito / nuovo exit node (retry se Tor riusa lo stesso) |
| `http_request(url, method, ...)` | una richiesta anonima: headers, params, data, json_body, cookies, impersonate, timeout, save_path, verify_tls, max_body_chars |
| `http_batch(calls, delay, stop_on_error, max_body_chars)` | N richieste in sequenza con passo; stesso schema di `http_request` per ogni elemento |
| `tor_selftest()` | verifica end-to-end senza toccare target (usa checkip.amazonaws.com) |

`http_request` ritorna un dizionario serializzabile:

```json
{"ok": true, "status": 200, "via": "tor", "final_url": "...", "headers": {...},
 "body": "...", "json": {...}, "bytes": 1234, "elapsed_ms": 812.3,
 "truncated": false, "saved_to": null}
```

I corpi oltre `max_body_chars` vengono troncati nella risposta e salvati interi in un
file temporaneo (`full_body_file`); `save_path` scrive il corpo completo dove dici tu.

Nei client che prefissano i tool con il nome del server, i nomi diventano
`mcp__tor_egress__http_request`, `mcp__tor_egress__tor_status`, …
(i trattini diventano underscore).

## Installazione

### In un colpo (script dall'URL raw)

```bash
curl -fsSL https://raw.githubusercontent.com/gidixi/tor-egress/main/install.sh | bash
```

Lo script scarica il pacchetto in `~/.local/share/tor-egress`, verifica/installa
`mcp` e `curl_cffi`, controlla il tunnel Tor e propone la registrazione in pi.

```bash
# con Tor configurato al volo (chiede sudo) e test finale:
curl -fsSL .../install.sh | bash -s -- --with-tor --check

# in una cartella diversa, senza toccare pi:
curl -fsSL .../install.sh | bash -s -- --dir ~/opt/tor-egress --no-pi

# aggiornare / disinstallare:
curl -fsSL .../install.sh | bash      # aggiorna il checkout esistente
bash ~/.local/share/tor-egress/install.sh --uninstall
```

Opzioni: `--dir PATH`, `--ref REF`, `--with-tor`, `--no-deps`, `--pi`, `--no-pi`,
`--check`, `--uninstall`, `--help` (variabili: `TOR_EGRESS_DIR`, `TOR_EGRESS_REF`,
`TOR_EGRESS_REPO`, `TOR_SOCKS`).

### 0. Prerequisiti (una volta per macchina)

```bash
sudo bash setup-tor.sh     # tor + SocksPort 9050 + ControlPort 9051 + mcp + curl_cffi
python3 selftest.py        # deve finire con "ESITO: OK"
```

`setup-tor.sh` è idempotente: non ricrea la password del ControlPort se
`~/.tor_ctrl_pass` esiste e fa un backup di `/etc/tor/torrc`.

### 1. Con pi (skill + MCP in un colpo)

```bash
pi install /percorso/di/tor-egress     # oppure: pi install -l ./tor-egress
pi mcp list                              # deve mostrare tor-egress con 6 tool
```

L'estensione registra il server a ogni avvio, quindi non serve toccare
`~/.pi/agent/mcp.json`. Se preferisci la configurazione statica (o un altro client),
usa lo snippet qui sotto.

### 2. Con qualsiasi client MCP (Claude Code/Desktop, Cursor, Codex, …)

```json
{
  "mcpServers": {
    "tor-egress": {
      "command": "python3",
      "args": ["/percorso/di/tor-egress/server/server.py"],
      "env": {
        "TOR_SOCKS_ENDPOINT": "127.0.0.1:9050",
        "TOR_CONTROL_ENDPOINT": "127.0.0.1:9051",
        "TOR_CTRL_PASSFILE": "~/.tor_ctrl_pass"
      }
    }
  }
}
```

Con pi da riga di comando:

```bash
pi mcp add tor-egress -- python3 /percorso/di/tor-egress/server/server.py
```

### 3. La skill, per agenti che non usano MCP

Copia `skills/tor-egress/` nella cartella skill dell'agente
(`.agents/skills/`, `.claude/skills/`, `~/.pi/agent/skills/`, …). La skill spiega
quali tool usare e quali regole rispettare; senza MCP punti a `bin/torcurl.py`.

## Configurazione (variabili d'ambiente del server MCP)

| Variabile | Default | Effetto |
|---|---|---|
| `TOR_SOCKS_ENDPOINT` | `127.0.0.1:9050` | dove ascolta il SOCKS di Tor |
| `TOR_CONTROL_ENDPOINT` | `127.0.0.1:9051` | ControlPort per NEWNYM |
| `TOR_CTRL_PASSFILE` | `~/.tor_ctrl_pass` | password del ControlPort (600) |
| `TOR_MIN_INTERVAL` | `1.2` | secondi minimi fra richieste consecutive |
| `TOR_MAX_BODY_CHARS` | `20000` | troncamento di default dei corpi |
| `TOR_ALLOW_DIRECT` | `0` | `1` abilita `allow_direct=true` (uscita senza Tor) |
| `TOR_MCP_PYTHON` | `python3` | interprete usato dall'estensione pi |

## Perché è sicuro per default

- **Guard sul tunnel**: prima di ogni richiesta controlla il SOCKS; se è giù solleva
  `TunnelDown` e non parte nulla. Nessun fallback silenzioso all'IP reale.
- **`socks5h` obbligatorio**: il DNS lo risolve l'exit node, quindi nessuna query DNS
  locale che riveli i domini visitati.
- **Uscita diretta impossibile per sbaglio**: `allow_direct=true` viene rifiutato con
  `DirectRefused` finché non imposti `TOR_ALLOW_DIRECT=1` nel server. È una decisione
  esplicita, non un default.
- **Un solo client condiviso**: passo minimo e sessioni vivono in un processo con lock,
  così un batch non può trasformarsi in una raffica.
- **Nessun disco per default**: cookie e header di sessione non vengono persistiti;
  i corpi troncati finiscono in file temporanei che il chiamante può cancellare.

## Limiti noti

- Tor non rende anonimi i *contenuti*: se ti autentichi con un account reale, il
  target sa chi sei. L'anonimato è di rete, non di identità applicativa.
- Il rate limiting moltiplica gli exit node: un 429 può dipendere dall'exit node
  condiviso con altri. `tor_newnym()` lo risolve spesso.
- Tor è lento (0,5–3 s per richiesta): per enumerazioni grandi conviene `http_batch`
  con `delay` realistico invece di centinaia di chiamate MCP.
- Non è un tool di port scan: SOCKS5 TCP connect scan via Tor è lento e rumoroso.

## Test

```bash
python3 selftest.py             # completo (rete: solo checkip.amazonaws.com)
python3 selftest.py --offline   # solo import/config/registrazione tool
python3 server/torhttp.py       # stato in JSON
python3 bin/torcurl.py --newnym
```

## Portarlo su un'altra macchina

1. copia la cartella (nessun path assoluto cablato dentro: tutto è relativo);
2. `sudo bash setup-tor.sh`;
3. `python3 selftest.py`;
4. `pi install ./tor-egress` oppure lo snippet `mcpServers`.

Se hai già Tor configurato, salta il punto 2 e imposta `TOR_SOCKS_ENDPOINT` /
`TOR_CONTROL_ENDPOINT` di conseguenza.
