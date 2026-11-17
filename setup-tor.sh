#!/usr/bin/env bash
# setup-tor.sh — prepara lo stack "HTTP anonimo" su una macchina nuova.
#
#   rete    : Tor client con SocksPort 127.0.0.1:9050 (DNS all'exit node)
#             e ControlPort 127.0.0.1:9051 + HashedControlPassword (per NEWNYM)
#   librerie: curl_cffi (fingerprint browser) e mcp (server stdio)
#
# Uso:  sudo bash setup-tor.sh
#       (poi: python3 selftest.py)
#
# Non installa exit relay: Tor resta un client. Modifica solo /etc/tor/torrc
# (con backup) e crea ~/.tor_ctrl_pass (600) dell'utente che ha lanciato sudo.
set -euo pipefail

SOCKS_PORT="${SOCKS_PORT:-9050}"
CTRL_PORT="${CTRL_PORT:-9051}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TORRC="/etc/tor/torrc"

# utente "vero" anche quando lo script gira con sudo
TARGET_USER="${SUDO_USER:-$(id -un)}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
PASSFILE="$TARGET_HOME/.tor_ctrl_pass"

say() { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }
die() { echo "ERRORE: $*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "serve root: sudo bash setup-tor.sh"

say "1/6  Pacchetti (tor, python3, pip)"
if command -v apt-get >/dev/null; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq tor python3 python3-pip curl >/dev/null
elif command -v dnf >/dev/null; then
  dnf install -y -q tor python3 python3-pip curl >/dev/null
elif command -v pacman >/dev/null; then
  pacman -Sy --noconfirm tor python python-pip curl >/dev/null
else
  die "package manager non riconosciuto: installa tor, python3, pip a mano"
fi

say "2/6  Config Tor (SocksPort + ControlPort)"
[ -f "$TORRC.orig-toranon" ] || cp "$TORRC" "$TORRC.orig-toranon" 2>/dev/null || true

# password casuale per il ControlPort, salvata fuori dal torrc (solo hash dentro)
if [ -f "$PASSFILE" ]; then
  CTRL_PASS="$(cat "$PASSFILE")"
else
  CTRL_PASS="$(head -c 24 /dev/urandom | base64 | tr -d '/+=' )"
  install -o "$TARGET_USER" -m 600 /dev/null "$PASSFILE"
  printf '%s' "$CTRL_PASS" > "$PASSFILE"
fi
HASH="$(tor --hash-password "$CTRL_PASS" | tail -1)"

grep -qs "^SocksPort 127.0.0.1:$SOCKS_PORT\$" "$TORRC" \
  || echo "SocksPort 127.0.0.1:$SOCKS_PORT" >> "$TORRC"
grep -qs "^ControlPort 127.0.0.1:$CTRL_PORT\$" "$TORRC" \
  || echo "ControlPort 127.0.0.1:$CTRL_PORT" >> "$TORRC"
if grep -qs '^HashedControlPassword' "$TORRC"; then
  sed -i "s|^HashedControlPassword.*|HashedControlPassword $HASH|" "$TORRC"
else
  echo "HashedControlPassword $HASH" >> "$TORRC"
fi

say "3/6  Avvio Tor"
if systemctl list-unit-files tor.service >/dev/null 2>&1; then
  systemctl enable --now tor
  systemctl restart tor
else
  nohup tor -f "$TORRC" >/var/log/tor-egress.log 2>&1 &
fi

say "4/6  Attesa bootstrap (max 60s)"
probe() {
  curl -s --max-time 15 --socks5-hostname "127.0.0.1:$SOCKS_PORT" \
       https://checkip.amazonaws.com 2>/dev/null || true
}
for _ in $(seq 1 30); do
  [ -n "$(probe)" ] && break
  sleep 2
done

say "5/6  Python: mcp + curl_cffi"
PIP=(python3 -m pip install -q -r "$ROOT/requirements.txt")
"${PIP[@]}" 2>/dev/null || python3 -m pip install -q --break-system-packages \
  -r "$ROOT/requirements.txt" || die "pip non è riuscito a installare mcp/curl_cffi"

say "6/6  Verifica"
VIA_TOR="$(probe)"
DIRETTO="$(curl -s --max-time 8 https://checkip.amazonaws.com 2>/dev/null || true)"
if [ -n "${VIA_TOR:-}" ] && [ "$VIA_TOR" != "${DIRETTO:-x}" ]; then
  echo "PASS — via Tor: $VIA_TOR | IP reale: ${DIRETTO:-?} (nascosto ai target)"
else
  echo "FALLITO — via Tor: '${VIA_TOR:-nessuna}' | diretto: ${DIRETTO:-nessuno}"
  echo "controlla il log di tor e che la porta $SOCKS_PORT sia in ascolto"
  exit 1
fi

if [ -f "$PASSFILE" ]; then
  chown "$TARGET_USER" "$PASSFILE"; chmod 600 "$PASSFILE"
  echo "password ControlPort: $PASSFILE (rotazione: python3 bin/torcurl.py --newnym)"
fi

cat <<'USAGE'

Fatto. Comandi utili:
  python3 selftest.py                      # handshake MCP + richieste reali via Tor
  python3 bin/torcurl.py --status          # stato tunnel / exit node
  python3 bin/torcurl.py --newnym          # nuovo exit node
  bin/acurl -sk 'https://target/path'      # curl forzato su Tor

MCP: vedi README.md (pi: `pi install /percorso/di/tor-egress`).
USAGE
