# personal-llm-assistant

A private chat service for family and friends at [openai.selvalogesh.com](https://openai.selvalogesh.com): Open
WebUI on the OMV home server, with
every model request going to OpenRouter through our own routing pipe, and voice handled on the server.

- **Four options in the model picker:** Auto, Creative, Smart and Free. Each is a chain of OpenRouter models that
  fails over to the next one. Providers that don't log prompts are asked first, and the Free chain answers when
  credits run out.
- **No buttons to find:** the Router works out from each message whether the reply should be an image, an answer
  from the web, code that runs in the browser, or a voice note (WhatsApp-style, once or for the rest of the chat).
  A cheap classifier decides first, models that take tools can choose too, and each person's misses teach their
  own classifier (CR-4).
- **Built in:** vision, document search, dictation and call mode (Whisper), read-aloud (Kokoro), and Python and SVG
  drawings.
- **Accounts:** sign-ups wait for the admin's approval.

| Where | What |
|---|---|
| [functions/](functions/) | The Router pipe and the voice-note filter (single-file Open WebUI functions) |
| [deploy/](deploy/) | Startup scripts, Open WebUI settings, the OMV compose stack and the [server runbook](deploy/README.md) |
| [tests/](tests/) | Offline tests (`python -m pytest`) and the classifier's labelled set |
| [tools/](tools/) | `eval_classifier.py`: the classifier against the labelled set, live (costs a fraction of a cent) |
| [context/](context/README.md) | Requirements, design decisions and the plan for each change request |
| [ENVIRONMENT.md](ENVIRONMENT.md) | Settings, secrets and local development |
