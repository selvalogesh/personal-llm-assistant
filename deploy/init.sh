#!/usr/bin/env bash
# The stack's one-shot `init` service (compose.yaml): builds the CHAT folder tree and the secrets it's missing, and
# fixes modes. Runs as root with no network before `webui` on every Up. It never overwrites a file. The only secret
# it creates itself is webui-secret-key, a random session key nobody needs to know; the others start as
# placeholders that `webui` refuses to start with.
set -euo pipefail

ROOT="${1:-/chat-storage}"
mkdir -p "$ROOT/data" "$ROOT/secrets"
chmod 700 "$ROOT/secrets"

placeholder() {
  local file="$ROOT/secrets/$1"
  if [[ ! -e "$file" ]]; then
    printf '%s\n' "$2" > "$file"
    echo "init: created placeholder $file"
  fi
}

placeholder openrouter-api-key "<paste the chat-server OpenRouter key here, alone on this line>"
placeholder webui-admin-password "<choose the admin password, alone on this line>"
if [[ ! -e "$ROOT/secrets/webui-secret-key" ]]; then
  head -c 48 /dev/urandom | base64 | tr -d '\n' > "$ROOT/secrets/webui-secret-key"
  echo "init: generated $ROOT/secrets/webui-secret-key"
fi

chmod 600 "$ROOT"/secrets/*
echo "init: ok"
