# personal-llm-assistant

A private chat service for family and friends at openai.selvalogesh.com: Open WebUI on the OMV home server, with
every model request going to OpenRouter through our own routing pipe, and voice handled on the server.

- **Four options in the model picker:** Auto, Creative, Smart and Free. Each is a chain of OpenRouter models that
  fails over to the next one. Providers that don't log prompts are asked first, and the Free chain answers when
  credits run out.
- **Toggles in the chat input:** Web search, Create image and Voice reply (a WhatsApp-style voice note under each
  reply).
- **Built in:** vision, document search, dictation and call mode (Whisper), read-aloud (Kokoro), and Python and SVG
  drawings.
- **Accounts:** sign-ups wait for the admin's approval.

| Where | What |
|---|---|
| [functions/](functions/) | The Router pipe and the three toggles (single-file Open WebUI functions) |
| [deploy/](deploy/) | Dockerfile, startup scripts, the OMV compose stack and the [server runbook](deploy/README.md) |
| [tests/](tests/) | Offline tests (`python -m pytest`) |
| [context/](context/README.md) | Requirements, design decisions and the plan for each change request |
| [ENVIRONMENT.md](ENVIRONMENT.md) | Settings, secrets and local development |
