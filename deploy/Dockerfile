# The chat service: Open WebUI, pinned, plus our functions and the startup scripts (CR-1 §3.5). Built and pushed to
# ghcr.io/selvalogesh/personal-llm-assistant by .github/workflows/image.yml; the OMV stack "Chat" runs it
# (deploy/compose.yaml). Bump the version here to upgrade Open WebUI, then check the runbook's "Done when".
FROM ghcr.io/open-webui/open-webui:v0.12.5

# Settings only, never secrets: those come from the read-only secrets folder at start (entrypoint.sh). Open WebUI
# saves most settings to its database on the first start; after that, change them in Admin → Settings.
ENV \
    # Only the Router's options appear (D6); every user sees them (R2)
    ENABLE_OPENAI_API=false \
    ENABLE_OLLAMA_API=false \
    BYPASS_MODEL_ACCESS_CONTROL=true \
    ENABLE_EVALUATION_ARENA_MODELS=false \
    ENABLE_COMMUNITY_SHARING=false \
    # Sign-ups wait for the admin's approval (R3); load_functions.py turns them back on after the admin is created
    ENABLE_SIGNUP=true \
    DEFAULT_USER_ROLE=pending \
    # Web search and image generation go through the Router's toggles, not Open WebUI's own engines
    ENABLE_WEB_SEARCH=false \
    ENABLE_IMAGE_GENERATION=false \
    # Document search: embeddings from OpenRouter (R10)
    RAG_EMBEDDING_ENGINE=openai \
    RAG_OPENAI_API_BASE_URL=https://openrouter.ai/api/v1 \
    RAG_EMBEDDING_MODEL=voyageai/voyage-4-lite \
    # Voice (R15): Whisper in this container, Kokoro in the stack
    AUDIO_STT_ENGINE= \
    WHISPER_MODEL=base.en \
    AUDIO_TTS_ENGINE=openai \
    AUDIO_TTS_OPENAI_API_BASE_URL=http://kokoro:8880/v1 \
    AUDIO_TTS_OPENAI_API_KEY=not-needed \
    AUDIO_TTS_MODEL=kokoro \
    AUDIO_TTS_VOICE=af_heart \
    # Python for charts and drawn images runs in the browser (R9)
    ENABLE_CODE_INTERPRETER=true \
    CODE_INTERPRETER_ENGINE=pyodide \
    CHAT_SECRETS_DIR=/run/chat-secrets \
    LOADER_FUNCTIONS_DIR=/app/chat-functions

COPY functions/ /app/chat-functions/
COPY deploy/entrypoint.sh deploy/init.sh deploy/load_functions.py /app/chat-start/

CMD ["bash", "/app/chat-start/entrypoint.sh"]
