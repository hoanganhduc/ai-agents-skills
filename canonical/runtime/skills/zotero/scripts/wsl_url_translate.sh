#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: wsl_url_translate.sh <url>" >&2
  exit 2
fi

URL="$1"
REPO="${ZOTERO_WSL_TRANSLATION_REPO:-${OPENCLAW_WSL_TRANSLATION_REPO:-$HOME/zotero-translation-server}}"
LOG="${ZOTERO_WSL_TRANSLATION_LOG:-$HOME/.cache/codex-zotero-translation-server.log}"

mkdir -p "$(dirname "$LOG")"

if [ ! -d "$REPO/.git" ]; then
  git clone --recurse-submodules https://github.com/zotero/translation-server "$REPO" >/dev/null 2>&1
fi

cd "$REPO"

if [ ! -d node_modules ]; then
  npm install >/dev/null 2>&1
fi

health_code() {
  curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:1969 || true
}

code="$(health_code)"
if [ "$code" != "200" ] && [ "$code" != "404" ]; then
  nohup npm start >"$LOG" 2>&1 &
  for _ in $(seq 1 60); do
    code="$(health_code)"
    if [ "$code" = "200" ] || [ "$code" = "404" ]; then
      break
    fi
    sleep 1
  done
fi

code="$(health_code)"
if [ "$code" != "200" ] && [ "$code" != "404" ]; then
  echo "WSL translation server did not become ready; see $LOG" >&2
  exit 1
fi

tmp="$(mktemp)"
http_code="$(
  curl -sS -o "$tmp" -w '%{http_code}' \
    -H 'Content-Type: text/plain' \
    --data-binary "$URL" \
    http://127.0.0.1:1969/web
)"

if [ "$http_code" != "200" ]; then
  cat "$tmp" >&2 || true
  rm -f "$tmp"
  exit 1
fi

cat "$tmp"
rm -f "$tmp"
