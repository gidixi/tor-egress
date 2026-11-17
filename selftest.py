#!/usr/bin/env python3
"""selftest.py — verifica del pacchetto tor-egress.

  python3 selftest.py            # completo: handshake MCP + richieste reali via Tor
  python3 selftest.py --offline  # solo import, config, registrazione tool (niente rete)

Il test completo parla solo con https://checkip.amazonaws.com (servizio di echo):
non contatta nessun target.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SERVER = HERE / "server" / "server.py"
sys.path.insert(0, str(HERE / "server"))


def _check(ok: bool, label: str, extra: str = "") -> bool:
    print(f"[{'PASS' if ok else 'FAIL'}] {label}{(' — ' + extra) if extra else ''}")
    return ok


async def async_main(offline: bool) -> int:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(command=sys.executable, args=[str(SERVER)],
                                   env=dict(os.environ))
    failures = 0
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            names = sorted(t.name for t in (await session.list_tools()).tools)
            expected = {"tor_status", "tor_exit_ip", "tor_newnym",
                        "http_request", "http_batch", "tor_selftest"}
            failures += not _check(expected <= set(names), "tool registrati", ", ".join(names))
            if offline:
                return failures

            res = await session.call_tool("tor_status", {})
            st = json.loads(res.content[0].text)
            failures += not _check(st.get("tunnel_up") is True, "tunnel Tor in ascolto",
                                   str(st.get("socks")))
            failures += not _check(st.get("exit_ip") not in (None, "", "(errore: )"),
                                   "exit node raggiungibile", str(st.get("exit_ip")))

            res = await session.call_tool("http_request", {
                "url": "https://checkip.amazonaws.com", "max_body_chars": 100})
            out = json.loads(res.content[0].text)
            failures += not _check(out.get("ok") and out.get("status") == 200,
                                   "GET anonima riuscita", f"HTTP {out.get('status')}")
            failures += not _check(out.get("via") == "tor", "richiesta passata dal tunnel",
                                   out.get("via", "?"))

            res = await session.call_tool("http_batch", {"calls": [
                {"url": "https://checkip.amazonaws.com", "max_body_chars": 60},
                {"url": "https://checkip.amazonaws.com", "max_body_chars": 60},
            ], "delay": 0.3})
            batch = json.loads(res.content[0].text)
            failures += not _check(batch.get("ok") and batch.get("count") == 2,
                                   "http_batch esegue in sequenza",
                                   f"{batch.get('count')} risposte, {batch.get('errors')} errori")
    return failures


def sync_checks() -> int:
    failures = 0
    try:
        import torhttp
        import curl_cffi  # noqa: F401
    except Exception as exc:                                      # noqa: BLE001
        print(f"[FAIL] import: {exc}")
        return 1
    cfg = torhttp.Config.from_env()
    failures += not _check(cfg.socks_port > 0 and "socks5h" in str(cfg.proxies),
                           "proxy socks5h configurato", str(cfg.proxies))
    failures += not _check(torhttp.Config.from_env().allow_direct is False
                           or os.environ.get("TOR_ALLOW_DIRECT") == "1",
                           "uscita diretta disattivata di default")
    if not SERVER.is_file():
        failures += not _check(False, "server.py presente")
    return failures


def main() -> int:
    offline = "--offline" in sys.argv
    failures = sync_checks()
    failures += asyncio.run(async_main(offline))
    print("\nESITO:", "OK" if failures == 0 else f"{failures} verifiche fallite")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
