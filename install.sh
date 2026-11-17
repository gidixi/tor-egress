#!/usr/bin/env bash
# tor-egress — installer
#
#   curl -fsSL https://raw.githubusercontent.com/gidixi/tor-egress/main/install.sh | bash
#   curl -fsSL .../install.sh | bash -s -- --with-tor --check
#
# Installa il pacchetto (MCP server + skill + CLI), verifica le dipendenze,
# controlla il tunnel Tor e, se vuoi, lo registra in pi.
# Non installa pacchetti di sistema senza `--with-tor` (che chiama setup-tor.sh con sudo).
set -euo pipefail

REPO="${TOR_EGRESS_REPO:-gidixi/tor-egress}"
REF="${TOR_EGRESS_REF:-main}"
DEST="${TOR_EGRESS_DIR:-$HOME/.local/share/tor-egress}"
MODE=install
WITH_TOR=0
WITH_DEPS=1
WITH_PI=auto
DO_CHECK=0
DEST_SET=0

SOCKS="${TOR_SOCKS:-127.0.0.1:9050}"

c_b="\033[1m"; c_g="\033[32m"; c_y="\033[33m"; c_r="\033[31m"; c_0="\033[0m"
say()  { printf "\n${c_b}== %s ==${c_0}\n" "$*"; }
ok()   { printf "${c_g}[ok]${c_0} %s\n" "$*"; }
warn() { printf "${c_y}[!]${c_0} %s\n" "$*"; }
die()  { printf "${c_r}[x]${c_0} %s\n" "$*" >&2; exit 1; }

usage() {
  cat <<EOF
tor-egress installer ($REPO@$REF)

Uso:
  curl -fsSL https://raw.githubusercontent.com/$REPO/$REF/install.sh | bash
  curl -fsSL .../install.sh | bash -s -- [opzioni]

Opzioni:
  --dir PATH     destinazione (default: $DEST)
  --ref REF      branch o tag (default: $REF)
  --with-tor     installa/configura Tor via setup-tor.sh (chiede sudo)
  --no-deps      non tocca le dipendenze Python (mcp, curl_cffi)
  --pi           registra il pacchetto in pi senza chiedere
  --no-pi        non registrare niente in pi
  --check        esegue selftest.py alla fine (una GET reale via Tor)
  --uninstall    rimuove l'installazione e la registrazione in pi
  -h, --help     questo testo

Variabili d'ambiente: TOR_EGRESS_DIR, TOR_EGRESS_REF, TOR_EGRESS_REPO, TOR_SOCKS.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dir)        DEST="${2:?--dir richiede un percorso}"; DEST_SET=1; shift 2 ;;
    --dir=*)      DEST="${1#*=}"; DEST_SET=1; shift ;;
    --ref)        REF="${2:?--ref richiede un valore}"; shift 2 ;;
    --ref=*)      REF="${1#*=}"; shift ;;
    --with-tor)   WITH_TOR=1; shift ;;
    --no-deps)    WITH_DEPS=0; shift ;;
    --pi)         WITH_PI=1; shift ;;
    --no-pi)      WITH_PI=0; shift ;;
    --check)      DO_CHECK=1; shift ;;
    --uninstall)  MODE=uninstall; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)            die "opzione sconosciuta: $1 (usa --help)" ;;
  esac
done

DEST="${DEST/#\~/$HOME}"

# ------------------------------------------------------- script locale o remoto
SELF_DIR=""
if [ -n "${BASH_SOURCE[0]:-}" ] && [ -f "${BASH_SOURCE[0]}" ]; then
  SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
LOCAL_SRC=""
if [ -n "$SELF_DIR" ] && [ -f "$SELF_DIR/package.json" ] \
   && grep -q '"name": *"tor-egress"' "$SELF_DIR/package.json" 2>/dev/null; then
  LOCAL_SRC="$SELF_DIR"
fi

# ---------------------------------------------------------------- uninstall
if [ "$MODE" = uninstall ]; then
  # lanciato da un'installazione locale senza --dir: rimuovi quella cartella
  if [ "$DEST_SET" = 0 ] && [ -n "$LOCAL_SRC" ]; then DEST="$LOCAL_SRC"; fi
  say "Rimozione"
  if command -v pi >/dev/null 2>&1; then
    pi remove "$DEST" >/dev/null 2>&1 || pi remove --local "$DEST" >/dev/null 2>&1 || true
  fi
  if [ -d "$DEST" ]; then rm -rf "$DEST"; ok "rimosso $DEST"; else warn "$DEST non esiste"; fi
  echo "Le modifiche a Tor (/etc/tor/torrc, ~/.tor_ctrl_pass) restano: rimuovile a mano se non servono."
  exit 0
fi

command -v python3 >/dev/null 2>&1 || die "serve python3 (>= 3.9)"
PYVER="$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])')"
python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,9) else 1)' \
  || die "python3 $PYVER troppo vecchio: serve >= 3.9"

say "1/5  Sorgente"
if [ -n "$LOCAL_SRC" ]; then
  if [ "$(cd "$LOCAL_SRC" && pwd)" = "$(cd "$DEST" 2>/dev/null && pwd || echo "")" ]; then
    ok "eseguo dall'installazione esistente ($LOCAL_SRC)"
  else
    mkdir -p "$DEST"
    cp -a "$LOCAL_SRC/." "$DEST/"
    ok "copiato da $LOCAL_SRC"
  fi
elif command -v git >/dev/null 2>&1; then
  command -v curl >/dev/null 2>&1 || die "serve curl (o git+curl) per scaricare il pacchetto"
  if [ -d "$DEST/.git" ]; then
    git -C "$DEST" fetch --depth 1 origin "$REF" >/dev/null 2>&1
    git -C "$DEST" checkout -q FETCH_HEAD
    git -C "$DEST" reset -q --hard FETCH_HEAD
    ok "aggiornato $DEST ($REF)"
  else
    mkdir -p "$(dirname "$DEST")"
    rm -rf "$DEST"
    git clone --quiet --depth 1 --branch "$REF" \
      "https://github.com/$REPO.git" "$DEST"
    ok "clonato $REPO@$REF in $DEST"
  fi
else
  command -v curl >/dev/null 2>&1 || die "serve curl"
  command -v tar  >/dev/null 2>&1 || die "serve tar"
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$REF" \
    | tar -xz -C "$TMP"
  mkdir -p "$DEST"
  rm -rf "$DEST"; mkdir -p "$DEST"
  cp -a "$TMP"/*/. "$DEST"/
  ok "scaricato tarball $REPO@$REF in $DEST"
fi

[ -f "$DEST/server/server.py" ] || die "installazione incompleta: manca $DEST/server/server.py"
[ -f "$DEST/skills/tor-egress/SKILL.md" ] || die "installazione incompleta: manca la skill"

# ------------------------------------------------------------- dipendenze
say "2/5  Dipendenze Python (mcp, curl_cffi)"
missing="$(python3 - "$DEST" <<'PY'
import sys
sys.path.insert(0, sys.argv[1] + "/server")
missing = []
for mod, pkg in (("mcp", "mcp"), ("curl_cffi", "curl_cffi")):
    try:
        __import__(mod)
    except Exception:
        missing.append(pkg)
print(" ".join(missing))
PY
)"
if [ -z "$missing" ]; then
  ok "già soddisfatte"
elif [ "$WITH_DEPS" = 0 ]; then
  warn "mancano: $missing (--no-deps: saltato)"
else
  pip_args=(-q install -r "$DEST/requirements.txt")
  if python3 -m pip "${pip_args[@]}" 2>/dev/null; then
    ok "installate con pip"
  elif python3 -m pip "${pip_args[@]}" --user 2>/dev/null; then
    ok "installate con pip --user"
  elif python3 -m pip "${pip_args[@]}" --break-system-packages 2>/dev/null; then
    ok "installate con pip --break-system-packages (PEP 668)"
  else
    die "pip non è riuscito a installare $missing: crea un venv e rilancia, oppure installa a mano (pip install mcp curl_cffi)"
  fi
fi

# ------------------------------------------------------------------ tor
say "3/5  Tunnel Tor ($SOCKS)"
tunnel_up() { (exec 3<>"/dev/tcp/${SOCKS%%:*}/${SOCKS##*:}") 2>/dev/null; }
if tunnel_up; then
  ok "SOCKS in ascolto su $SOCKS"
elif [ "$WITH_TOR" = 1 ]; then
  say "setup-tor.sh (sudo)"
  sudo bash "$DEST/setup-tor.sh"
  tunnel_up && ok "tunnel attivo" || die "Tor non è partito: controlla il log"
else
  warn "nessun SOCKS su $SOCKS."
  warn "Installa/configura Tor con:  sudo bash \"$DEST/setup-tor.sh\""
  warn "oppure rilancia questo installer con --with-tor"
fi

# ------------------------------------------------------------------- pi
say "4/5  Registrazione nel client MCP"
register_pi() {
  command -v pi >/dev/null 2>&1 || return 1
  if [ "$WITH_PI" = 1 ]; then
    pi install "$DEST"
  else
    pi install "$DEST" < /dev/tty > /dev/tty 2>&1 || return 1
  fi
}
if [ "$WITH_PI" = 0 ]; then
  warn "registrazione in pi saltata (--no-pi)"
elif ! command -v pi >/dev/null 2>&1; then
  warn "pi non è installato: usa lo snippet mcpServers qui sotto"
elif [ "$WITH_PI" = 1 ]; then
  pi install "$DEST" >/dev/null && ok "registrato in pi (skill + MCP server)"
else
  if [ -t 0 ] && [ -t 1 ]; then
    printf "${c_b}Registro il pacchetto in pi? [S/n] ${c_0}"
    read -r ans </dev/tty || ans=S
    case "${ans:-S}" in
      [nN]*) warn "saltato: registralo poi con  pi install $DEST" ;;
      *)     pi install "$DEST" >/dev/null && ok "registrato in pi (skill + MCP server)" ;;
    esac
  else
    warn "stdin non interattivo: registra con  pi install $DEST"
  fi
fi

cat <<EOF

Altri client MCP (Claude Code/Desktop, Cursor, Codex, …) — aggiungi a mcpServers:
{
  "mcpServers": {
    "tor-egress": {
      "command": "python3",
      "args": ["$DEST/server/server.py"]
    }
  }
}

CLI di servizio:
  python3 $DEST/bin/torcurl.py --status
  $DEST/bin/acurl -sk 'https://example.com'
EOF

# ---------------------------------------------------------------- check
say "5/5  Verifica"
if [ "$DO_CHECK" = 1 ]; then
  python3 "$DEST/selftest.py" || die "selftest fallito"
else
  python3 "$DEST/bin/torcurl.py" --status 2>/dev/null || warn "stato non disponibile"
  echo "selftest completo:  python3 $DEST/selftest.py"
fi

say "Fatto"
echo "Pacchetto: $DEST"
echo "Skill per altri agenti: $DEST/skills/tor-egress/"
echo "Aggiornare: rilancia questo installer (aggiorna il checkout) oppure: pi update --extensions"
