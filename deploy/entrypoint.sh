#!/usr/bin/env bash
# Starts the official Open WebUI image from our files in the fetched copy (CR-2 §3.3): loads our settings, checks
# the Open WebUI version, reads the secrets from the read-only secrets folder, and loads our functions once it's
# up. Secrets are exported to this process only: they never appear in the stack environment or `docker inspect`.
set -euo pipefail

APP="$(cd "$(dirname "$0")/.." && pwd)"
SECRETS="${CHAT_SECRETS_DIR:-/run/chat-secrets}"

# Our settings over the image's defaults; plain KEY=value lines (deploy/settings.env)
set -a
. "$APP/deploy/settings.env"
set +a

# Stops when the stack's Open WebUI isn't the version the repo pins, and logs the running commit
CHECK_APP_DIR="$APP" python "$APP/deploy/check_version.py"

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
export WEBUI_SECRET_KEY WEBUI_ADMIN_PASSWORD
# Only the Router uses the OpenRouter key, reading the file itself on every request (CR-3: documents embed on the
# server). Checked here only so a placeholder stops the start with a clear message, not the first chat.
read_secret openrouter-api-key > /dev/null
# Kokoro needs no key, but Open WebUI's OpenAI-style speech client sends one (set here, not in settings.env, where
# the name trips the secrets linter).
export AUDIO_TTS_OPENAI_API_KEY="${AUDIO_TTS_OPENAI_API_KEY:-not-needed}"

LOADER_FUNCTIONS_DIR="$APP/functions" python "$APP/deploy/load_functions.py" &

cd /app/backend
exec bash start.sh
