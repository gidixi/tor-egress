#!/usr/bin/env python3
"""torhttp.py — HTTP anonimo: Tor SOCKS5h + fingerprint TLS di browser reale.

Nucleo riusabile (estratto da `scan/anon_http.py`), senza dipendenze dal
progetto di origine. Garanzie:

  * proxy SOCKS5h obbligatorio (DNS risolto dall'exit node, niente leak);
  * se il tunnel non è in ascolto la richiesta NON parte (eccezione TunnelDown):
    nessuna uscita accidentale con l'IP reale;
  * fingerprint TLS/HTTP2 di un browser reale via curl_cffi (impersonate);
  * passo minimo configurabile fra richieste consecutive + retry con backoff
    su 429/502/503/504;
  * rotazione circuito (NEWNYM) via ControlPort, se la password è disponibile.

Configurazione da ambiente (tutte opzionali):
  TOR_SOCKS_ENDPOINT     "127.0.0.1:9050"
  TOR_CONTROL_ENDPOINT   "127.0.0.1:9051"
  TOR_CTRL_PASSFILE      "~/.tor_ctrl_pass"
  TOR_MIN_INTERVAL       "1.2"    secondi fra richieste consecutive
  TOR_MAX_BODY_CHARS     "20000"  troncamento di default dei corpi restituiti
  TOR_ALLOW_DIRECT       "0"      "1" autorizza esplicitamente le uscite dirette

Uso da codice:
  import torhttp
  torhttp.get("https://example.com", params={"q": "x"})
  torhttp.client().newnym()
"""
from __future__ import annotations

import base64
import json as jsonlib
import os
import socket
import tempfile
import threading
import time
import warnings
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as cffi

warnings.filterwarnings("ignore")

TRUTHY = {"1", "true", "yes", "on"}
TEXTUAL = (
    "text/",
    "application/json",
    "application/xml",
    "application/xhtml",
    "application/javascript",
    "application/x-javascript",
    "application/x-www-form-urlencoded",
    "application/ld+json",
    "application/rss+xml",
    "application/atom+xml",
    "image/svg+xml",
)


class TunnelDown(RuntimeError):
    """SOCKS in ascolto assente: meglio fermarsi che uscire in chiaro."""


class DirectRefused(RuntimeError):
    """Richiesta di uscita diretta non autorizzata."""


def _split_endpoint(raw: str, default_port: int) -> tuple[str, int]:
    raw = raw.strip()
    if not raw:
        return "127.0.0.1", default_port
    host, sep, port = raw.rpartition(":")
    if not sep:
        return raw, default_port
    return host or "127.0.0.1", int(port)


@dataclass
class Config:
    socks_host: str = "127.0.0.1"
    socks_port: int = 9050
    control_host: str = "127.0.0.1"
    control_port: int = 9051
    passfile: str = ""
    min_interval: float = 1.2
    max_body_chars: int = 20000
    allow_direct: bool = False

    @classmethod
    def from_env(cls) -> "Config":
        socks_host, socks_port = _split_endpoint(
            os.environ.get("TOR_SOCKS_ENDPOINT", "127.0.0.1:9050"), 9050)
        ctl_host, ctl_port = _split_endpoint(
            os.environ.get("TOR_CONTROL_ENDPOINT", "127.0.0.1:9051"), 9051)
        return cls(
            socks_host=socks_host,
            socks_port=socks_port,
            control_host=ctl_host,
            control_port=ctl_port,
            passfile=os.path.expanduser(
                os.environ.get("TOR_CTRL_PASSFILE", "~/.tor_ctrl_pass")),
            min_interval=float(os.environ.get("TOR_MIN_INTERVAL", "1.2")),
            max_body_chars=int(os.environ.get("TOR_MAX_BODY_CHARS", "20000")),
            allow_direct=os.environ.get("TOR_ALLOW_DIRECT", "0").lower() in TRUTHY,
        )

    @property
    def proxies(self) -> dict:
        return {"all": f"socks5h://{self.socks_host}:{self.socks_port}"}


class TorClient:
    """Client HTTP anonimo, thread-safe, con passo minimo condiviso."""

    def __init__(self, cfg: Config | None = None) -> None:
        self.cfg = cfg or Config.from_env()
        self._lock = threading.Lock()
        self._sessions: dict[tuple[str, bool], object] = {}
        self._last_request = 0.0

    # ------------------------------------------------------------------ tunnel
    def tunnel_up(self, timeout: float = 1.0) -> bool:
        try:
            socket.create_connection(
                (self.cfg.socks_host, self.cfg.socks_port), timeout=timeout).close()
            return True
        except OSError:
            return False

    def control_open(self, timeout: float = 1.0) -> bool:
        try:
            socket.create_connection(
                (self.cfg.control_host, self.cfg.control_port), timeout=timeout).close()
            return True
        except OSError:
            return False

    def password(self) -> str | None:
        for candidate in (self.cfg.passfile, "~/.tor_ctrl_pass"):
            p = Path(candidate).expanduser()
            if p.is_file():
                return p.read_text().strip()
        return None

    def _resolve_direct(self, allow_direct: bool) -> bool:
        if allow_direct and not self.cfg.allow_direct:
            raise DirectRefused(
                "uscita diretta non autorizzata: impostare TOR_ALLOW_DIRECT=1 "
                "nell'ambiente del server MCP per abilitarla")
        return allow_direct

    def _require_tunnel(self) -> None:
        if not self.tunnel_up():
            raise TunnelDown(
                f"nessun SOCKS su {self.cfg.socks_host}:{self.cfg.socks_port} — "
                "avviare Tor (vedi setup-tor.sh)")

    # ----------------------------------------------------------------- session
    def session(self, impersonate: str = "chrome", verify: bool = True):
        key = (impersonate, verify)
        s = self._sessions.get(key)
        if s is None:
            s = cffi.Session(impersonate=impersonate, verify=verify)
            self._sessions[key] = s
        return s

    def _pace(self, delay: float | None = None) -> None:
        """Dorme quanto basta perché due richieste non siano più vicine del passo."""
        step = self.cfg.min_interval if delay is None else delay
        if step <= 0:
            return
        wait = self._last_request + step - time.monotonic()
        if wait > 0:
            time.sleep(wait)

    # ----------------------------------------------------------------- request
    def request(self, method: str, url: str, *, headers: dict | None = None,
                params: dict | None = None, data=None, json=None,
                impersonate: str = "chrome", timeout: float = 60.0,
                allow_redirects: bool = True, verify: bool = True,
                allow_direct: bool = False, retries: int = 4,
                delay: float | None = None, cookies: dict | None = None,
                save_path: str | None = None,
                max_body_chars: int | None = None) -> dict:
        """Esegue la richiesta e ritorna un dizionario pronto da serializzare."""
        direct = self._resolve_direct(allow_direct)
        if not direct:
            self._require_tunnel()

        method = method.upper()
        max_chars = self.cfg.max_body_chars if max_body_chars is None else max_body_chars
        attempt = 0
        last_error = None

        with self._lock:
            while attempt < retries:
                attempt += 1
                self._pace(delay)
                started = time.monotonic()
                try:
                    r = self.session(impersonate, verify).request(
                        method, url, params=params, data=data, json=json,
                        headers=headers, cookies=cookies, timeout=timeout,
                        allow_redirects=allow_redirects,
                        proxies=None if direct else self.cfg.proxies,
                    )
                except Exception as exc:                          # noqa: BLE001
                    last_error = f"{type(exc).__name__}: {exc}"
                    self._last_request = time.monotonic()
                    if attempt < retries:
                        time.sleep(min(2 * attempt, 8))
                    continue
                self._last_request = time.monotonic()

                if r.status_code in (429, 502, 503, 504) and attempt < retries:
                    backoff = 7 * attempt if r.status_code == 429 else 2 * attempt
                    time.sleep(backoff)
                    continue

                return self._result(r, method, url, direct, attempt,
                                    time.monotonic() - started, save_path, max_chars)

        return {
            "ok": False,
            "error": last_error or "richiesta non riuscita",
            "attempts": attempt,
            "method": method,
            "url": url,
            "via": "direct" if direct else "tor",
        }

    def _result(self, r, method: str, url: str, direct: bool, attempts: int,
                elapsed: float, save_path: str | None, max_chars: int) -> dict:
        raw = r.content or b""
        ctype = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
        body, encoding = self._decode(raw, ctype, max_chars)
        out = {
            "ok": 200 <= r.status_code < 400,
            "status": r.status_code,
            "method": method,
            "url": url,
            "final_url": str(r.url),
            "via": "direct" if direct else "tor",
            "attempts": attempts,
            "elapsed_ms": round(elapsed * 1000, 1),
            "bytes": len(raw),
            "content_type": ctype or None,
            "encoding": encoding,
            "headers": {k: v for k, v in r.headers.items()},
            "body": body,
        }
        if "json" in ctype and raw:
            try:
                parsed = jsonlib.loads(raw)
            except ValueError:
                pass
            else:
                if isinstance(parsed, (dict, list)):
                    out["json"] = parsed
        out["truncated"] = len(body) >= max_chars if body else False

        if save_path:
            p = Path(save_path).expanduser()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
            out["saved_to"] = str(p.resolve())
        elif out["truncated"]:
            fd, tmp = tempfile.mkstemp(prefix="toranon-", suffix=".out")
            with os.fdopen(fd, "wb") as fh:
                fh.write(raw)
            out["full_body_file"] = tmp
        return out

    @staticmethod
    def _decode(raw: bytes, ctype: str, max_chars: int) -> tuple[str, str]:
        if not raw:
            return "", "utf-8"
        textual = (not ctype) or ctype.startswith(TEXTUAL) or "json" in ctype or "xml" in ctype
        if textual:
            text = raw.decode("utf-8", errors="replace")
            return text[:max_chars], "utf-8"
        b64 = base64.b64encode(raw[: max(0, max_chars * 3 // 4)]).decode()
        return b64, "base64"

    # ------------------------------------------------------------------ torops
    def _control(self, *cmds: str, timeout: float = 15.0) -> list[tuple[str, str]]:
        pwd = self.password()
        if not pwd:
            raise RuntimeError(
                f"password del ControlPort non trovata ({self.cfg.passfile})")
        s = socket.create_connection(
            (self.cfg.control_host, self.cfg.control_port), timeout=timeout)
        f = s.makefile("rwb")

        def send(line: str) -> tuple[str, str]:
            f.write((line + "\r\n").encode())
            f.flush()
            parts: list[str] = []
            while True:
                reply = f.readline().decode(errors="replace").rstrip("\r\n")
                if len(reply) < 4:
                    return "???", "risposta vuota"
                parts.append(reply[4:])
                if reply[3] == " ":
                    return reply[:3], "\n".join(parts)

        try:
            send(f'AUTHENTICATE "{pwd}"')
            replies = [send(c) for c in cmds]
        finally:
            try:
                send("QUIT")
            except OSError:
                pass
            s.close()
        return replies

    def newnym(self, wait: float = 4.0, verify: bool = True,
               attempts: int = 3) -> dict:
        """Chiede un nuovo circuito (nuovo exit node) e, se richiesto, verifica.

        Tor a volte riusa lo stesso exit node: si ripete NEWNYM fino a `attempts`
        volte. La sessione HTTP viene svuotata per non riusare una connessione
        aperta sul circuito vecchio.
        """
        out: dict = {"ok": False, "attempts": 0}
        if not self.control_open():
            out["error"] = (f"ControlPort non in ascolto su "
                            f"{self.cfg.control_host}:{self.cfg.control_port}")
            return out
        before = self.exit_ip() if verify else None
        for i in range(1, max(1, attempts) + 1):
            out["attempts"] = i
            try:
                replies = self._control("SIGNAL NEWNYM")
                out["reply"] = f"{replies[0][0]} {replies[0][1]}"
                out["ok"] = replies[0][0] == "250"
            except Exception as exc:                              # noqa: BLE001
                out["error"] = f"{type(exc).__name__}: {exc}"
                return out
            if not verify:
                return out
            self._sessions.clear()
            time.sleep(wait)
            after = self.exit_ip()
            out.update(before=before, after=after,
                       rotated=bool(before and after and before != after))
            if out["rotated"]:
                return out
        out["note"] = "stesso exit node dopo i tentativi: riprova tra qualche secondo"
        return out

    def exit_ip(self, allow_direct: bool = False, timeout: float = 25.0) -> str:
        try:
            r = self.request("GET", "https://checkip.amazonaws.com",
                             timeout=timeout, retries=2, allow_direct=allow_direct,
                             max_body_chars=100)
        except Exception as exc:                                  # noqa: BLE001
            return f"(errore: {exc})"
        return (r.get("body") or "").strip() or f"(HTTP {r.get('status')})"

    def status(self, check_direct: bool = False) -> dict:
        pwd = self.password()
        out = {
            "tunnel_up": self.tunnel_up(),
            "socks": f"{self.cfg.socks_host}:{self.cfg.socks_port}",
            "control_open": self.control_open(),
            "control": f"{self.cfg.control_host}:{self.cfg.control_port}",
            "control_password": bool(pwd),
            "min_interval_s": self.cfg.min_interval,
            "direct_allowed": self.cfg.allow_direct,
            "exit_ip": None,
            "direct_ip": None,
        }
        if out["tunnel_up"]:
            out["exit_ip"] = self.exit_ip()
            out["anonymous"] = bool(out["exit_ip"]) and not out["exit_ip"].startswith("(")
        else:
            out["anonymous"] = False
            out["error"] = "tunnel Tor assente: nessuna richiesta può partire"
        if check_direct:
            out["direct_ip"] = self.exit_ip(allow_direct=True)
            out["leak"] = out["direct_ip"] == out["exit_ip"]
        return out


_CLIENT: TorClient | None = None
_CLIENT_LOCK = threading.Lock()


def client(cfg: Config | None = None) -> TorClient:
    global _CLIENT
    if cfg is not None:
        return TorClient(cfg)
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = TorClient()
        return _CLIENT


def request(method: str, url: str, **kw) -> dict:
    return client().request(method, url, **kw)


def get(url: str, **kw) -> dict:
    return request("GET", url, **kw)


def post(url: str, **kw) -> dict:
    kw.setdefault("headers", {})
    kw["headers"].setdefault("Content-Type", "application/x-www-form-urlencoded")
    return request("POST", url, **kw)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="stato del tunnel Tor anonimo")
    ap.add_argument("--check-direct", action="store_true",
                    help="confronta l'IP reale (uscita diretta, TOR_ALLOW_DIRECT=1)")
    ap.add_argument("--newnym", action="store_true", help="ruota il circuito")
    args = ap.parse_args()
    cli = client()
    if args.newnym:
        print(jsonlib.dumps(cli.newnym(), indent=2))
    else:
        print(jsonlib.dumps(cli.status(args.check_direct), indent=2))
