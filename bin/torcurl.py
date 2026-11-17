#!/usr/bin/env python3
"""torcurl.py — CLI equivalente ai tool MCP (fallback quando l'MCP non è caricato).

Uso:
  torcurl.py GET  https://target/path [-H 'K: V']... [-q] [-o FILE]
  torcurl.py POST https://target/api --data 'a=1&b=2' [-H 'Content-Type: ...']
  torcurl.py POST https://target/api --json '{"a":1}'
  torcurl.py GET  https://target/path --direct        # solo se TOR_ALLOW_DIRECT=1
  torcurl.py --status                                 # stato tunnel / exit node
  torcurl.py --newnym                                 # nuovo circuito

Opzioni: --impersonate chrome|firefox|safari|edge, -k/--insecure, --timeout,
         --allow-redirects/--no-redirects, -q (solo corpo), -o (salva su file).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import torhttp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="richieste HTTP anonime via Tor")
    ap.add_argument("method", nargs="?", default="GET")
    ap.add_argument("url", nargs="?")
    ap.add_argument("-H", "--header", action="append", default=[], metavar="K: V")
    ap.add_argument("--data", default=None, help="body grezzo")
    ap.add_argument("--json", dest="json_body", default=None, help="body JSON")
    ap.add_argument("--impersonate", default="chrome")
    ap.add_argument("-k", "--insecure", action="store_true")
    ap.add_argument("--direct", action="store_true", help="NON passare da Tor")
    ap.add_argument("-q", "--quiet", action="store_true", help="stampa solo il corpo")
    ap.add_argument("-o", "--output", default=None, help="salva il corpo su file")
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--no-redirects", action="store_true")
    ap.add_argument("--status", action="store_true", help="stato del tunnel ed esce")
    ap.add_argument("--check-direct", action="store_true",
                    help="con --status: confronta anche l'IP reale")
    ap.add_argument("--newnym", action="store_true", help="ruota il circuito ed esce")
    a = ap.parse_args()

    cli = torhttp.client()

    if a.status:
        print(json.dumps(cli.status(a.check_direct), indent=2))
        return 0
    if a.newnym:
        print(json.dumps(cli.newnym(), indent=2))
        return 0
    if not a.url:
        ap.error("url mancante")

    headers = {}
    for h in a.header:
        k, _, v = h.partition(":")
        headers[k.strip()] = v.strip()

    body = None
    if a.json_body is not None:
        body = json.loads(a.json_body)
    try:
        r = cli.request(
            a.method, a.url, headers=headers or None, data=a.data, json=body,
            impersonate=a.impersonate, timeout=a.timeout,
            allow_redirects=not a.no_redirects, verify=not a.insecure,
            allow_direct=a.direct, save_path=a.output,
        )
    except (torhttp.TunnelDown, torhttp.DirectRefused) as exc:
        print(f"errore: {exc}", file=sys.stderr)
        return 3

    if a.quiet:
        print(r.get("body", ""), end="")
        return 0 if r.get("ok") else 1

    meta = {k: r.get(k) for k in ("ok", "status", "via", "final_url", "bytes",
                                  "elapsed_ms", "content_type", "saved_to",
                                  "full_body_file", "error", "attempts")}
    print("# " + json.dumps({k: v for k, v in meta.items() if v is not None}))
    print()
    print(r.get("body", ""))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
