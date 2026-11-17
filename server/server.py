#!/usr/bin/env python3
"""server.py — MCP server (stdio) "tor-egress".

Espone un client HTTP che passa *sempre* dal tunnel Tor locale con fingerprint
da browser reale, più il controllo del tunnel e la rotazione del circuito.

Tool:
  tor_status(**check_direct**)   stato tunnel/exit node (nessuna richiesta al target)
  tor_exit_ip()                  IP con cui ci vede il mondo
  tor_newnym(verify)             nuovo circuito (nuovo exit node)
  http_request(**...)            una richiesta HTTP(S) anonima
  http_batch(calls, **...)       più richieste in sequenza, con passo

Trasporto: stdio (stdout riservato al protocollo, log su stderr).
Ambiente: vedi torhttp.Config.from_env / README.md.
"""
from __future__ import annotations

import json
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

import torhttp

INSTRUCTIONS = """\
Ogni richiesta HTTP(S) verso l'esterno deve passare da questi tool: usano il
tunnel Tor locale (SOCKS5h, DNS risolto dall'exit node) con fingerprint TLS/HTTP2
da browser reale, così i server remoti vedono l'IP di un exit node Tor.

Regole:
- Preflight a inizio sessione: tor_status. Se tunnel_up è false, fermarsi.
- Usa http_request per richieste singole e http_batch per sequenze (loop,
  enumerazioni, polling): il passo minimo fra richieste è già applicato.
- Percent-encode i payload che finiscono nell'URL (spazi, apici, parentesi).
- Non usare curl/wget/requests diretti: aggirano il tunnel e lasciano l'IP reale.
- L'uscita diretta è possibile solo se TOR_ALLOW_DIRECT=1 nel server e se il
  chiamante passa allow_direct=true: non è un default.
- Sul 429 lo strumento attende e riprova; se persiste, ruota l'identità con
  tor_newnym e riprova più lentamente.
"""

mcp = FastMCP("tor-egress", instructions=INSTRUCTIONS, log_level="WARNING")
_client = torhttp.client()


def _json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False, default=str)


@mcp.tool()
def tor_status(check_direct: bool = False) -> dict:
    """Stato del tunnel: SOCKS, ControlPort, exit node Tor corrente.

    Va chiamato prima di qualsiasi altra richiesta. `check_direct=true` fa una
    singola uscita diretta verso il servizio di echo per confrontare l'IP reale
    (espone l'IP reale a quel servizio; richiede TOR_ALLOW_DIRECT=1).
    """
    return _client.status(check_direct=check_direct)


@mcp.tool()
def tor_exit_ip() -> dict:
    """IP pubblico visto dall'esterno passando dal tunnel Tor (exit node)."""
    return {"exit_ip": _client.exit_ip(), "socks": f"{_client.cfg.socks_host}:{_client.cfg.socks_port}"}


@mcp.tool()
def tor_newnym(verify: bool = True, wait: float = 4.0) -> dict:
    """Ruota il circuito Tor (SIGNAL NEWNYM): nuovo exit node, nuovo budget di rate limit.

    Usalo dopo 429 ostinati, su exit node bloccati/403 o prima di una fase nuova.
    `verify` confronta l'exit node prima e dopo.
    """
    return _client.newnym(wait=wait, verify=verify)


@mcp.tool()
def http_request(
    url: str,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    data: str | None = None,
    json_body: Any = None,
    cookies: dict[str, str] | None = None,
    impersonate: str = "chrome",
    timeout: float = 60.0,
    allow_redirects: bool = True,
    verify_tls: bool = True,
    save_path: str | None = None,
    max_body_chars: int | None = None,
    allow_direct: bool = False,
) -> dict:
    """Richiesta HTTP(S) anonima via Tor, con fingerprint da browser reale.

    Ritorna {ok, status, final_url, headers, body, json?, bytes, elapsed_ms,
    via: "tor", truncated?, full_body_file?, saved_to?}.

    - `params` finisce nella query string; `data` è il body grezzo (es.
      "a=1&b=2" o JSON testuale); `json_body` invia un body application/json.
    - `impersonate`: chrome | firefox | safari | edge | chrome110 | ... (curl_cffi).
    - `max_body_chars`: tronca il corpo nella risposta; se troncato il corpo
      completo è scritto in un file temporaneo (campo full_body_file).
    - `save_path`: salva il corpo completo su file (utile per download/binari).
    - `allow_direct=true` esce senza Tor: richiede TOR_ALLOW_DIRECT=1.
    - `verify_tls=false` equivale a `curl -k`.
    """
    return _client.request(
        method, url, headers=headers, params=params, data=data, json=json_body,
        cookies=cookies, impersonate=impersonate, timeout=timeout,
        allow_redirects=allow_redirects, verify=verify_tls,
        save_path=save_path, max_body_chars=max_body_chars,
        allow_direct=allow_direct,
    )


@mcp.tool()
def http_batch(
    calls: list[dict[str, Any]],
    delay: float | None = None,
    stop_on_error: bool = False,
    max_body_chars: int = 2000,
) -> dict:
    """Esegue in sequenza una lista di richieste anonime, rispettando il passo.

    Ogni elemento di `calls` accetta gli stessi argomenti di http_request
    (url, method, headers, params, data, json_body, ...), con `json_body`
    o `json` indifferentemente. Ritorna {ok, count, errors, results}.
    Utile per enumerazioni, polling e confronti: un'unica chiamata MCP invece
    di N chiamate, con il pacing applicato dal client.
    """
    if not isinstance(calls, list) or not calls:
        return {"ok": False, "error": "calls deve essere una lista non vuota"}
    if len(calls) > 200:
        return {"ok": False, "error": f"troppe richieste in un batch: {len(calls)} (max 200)"}

    results: list[dict] = []
    errors = 0
    for i, raw in enumerate(calls, 1):
        if not isinstance(raw, dict) or not raw.get("url"):
            results.append({"ok": False, "index": i, "error": "call senza url", "input": raw})
            errors += 1
            continue
        kw = dict(raw)
        body = kw.pop("json", None)
        kw.setdefault("method", "GET")
        if body is not None and kw.get("json_body") is None:
            kw["json_body"] = body
        kw.setdefault("max_body_chars", max_body_chars)
        try:
            res = _client.request(**kw)
        except Exception as exc:                                  # noqa: BLE001
            res = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "url": raw["url"]}
        res["index"] = i
        if not res.get("ok"):
            errors += 1
        results.append(res)
        if errors and stop_on_error:
            break
    return {"ok": errors == 0, "count": len(results), "errors": errors, "results": results}


@mcp.tool()
def tor_selftest() -> dict:
    """Verifica end-to-end: tunnel, exit node, una GET reale via Tor.

    Non tocca nessun target: usa https://checkip.amazonaws.com.
    """
    st = _client.status()
    probe = _client.request("GET", "https://checkip.amazonaws.com",
                            timeout=30, retries=2, max_body_chars=100)
    return {"status": st,
            "probe": {k: probe.get(k) for k in
                      ("ok", "status", "body", "via", "elapsed_ms", "error")}}


def main() -> int:
    if "--selftest" in sys.argv:
        print(_json(tor_selftest()))
        return 0
    print("[tor-egress] MCP server stdio pronto", file=sys.stderr)
    mcp.run("stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
