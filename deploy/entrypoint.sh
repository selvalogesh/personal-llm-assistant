#!/usr/bin/env bash
# Starts Open WebUI with the secrets from the read-only secrets folder, and loads our functions once it's up
# (CR-1 §3.5). Secrets are exported to this process only: they never appear in the image, the stack environment
# or `docker inspect`.
set -euo pipefail

SECRETS="${CHAT_SECRETS_DIR:-/run/chat-secrets}"

# Prints the secret's value; stops the container with a clear message when the file is missing or a placeholder.
read_secret() {
  local file="$SECRETS/$1" value
  if [[ ! -s "$file" ]]; then
    echo "entrypoint: $file is missing or empty. Fill it in (deploy/README.md, step 3), then Up again." >&2
    exit 1
  fi
  value="$(tr -d '\r\n' < "$file")"
  if [[ -z "$value" || "$value" == "<"* ]]; then
    echo "entrypoint: $file still holds the placeholder. Fill it in (deploy/README.md, step 3), then Up again." >&2
    exit 1
  fi
  printf '%s' "$value"
}

: "${WEBUI_ADMIN_EMAIL:?set WEBUI_ADMIN_EMAIL in the stack environment}"
WEBUI_SECRET_KEY="$(read_secret webui-secret-key)"
WEBUI_ADMIN_PASSWORD="$(read_secret webui-admin-password)"
# The Router reads the key file itself on every request; document search needs it in Open WebUI's settings.
RAG_OPENAI_API_KEY="$(read_secret openrouter-api-key)"
export WEBUI_SECRET_KEY WEBUI_ADMIN_PASSWORD RAG_OPENAI_API_KEY
# Kokoro needs no key, but Open WebUI's OpenAI-style speech client sends one (set here, not in the Dockerfile,
# where the name trips the secrets linter).
export AUDIO_TTS_OPENAI_API_KEY="${AUDIO_TTS_OPENAI_API_KEY:-not-needed}"

python /app/chat-start/load_functions.py &

cd /app/backend
exec bash start.sh
