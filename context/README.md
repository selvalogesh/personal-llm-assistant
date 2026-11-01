# Change requests

Each feature request (CR) has one plan doc, `context/CR-<n>.md`, written before any code. A plan holds what the
request asks for and where each ask comes from, its design decisions with their reasons, the build steps with
their state, how it's verified, and a decisions log. The convention comes from finance-analysis (its CR-6).

[Discovery.md](Discovery.md) summarises the initial ideation that came before CR-1: hosting options, providers,
OpenRouter keys and budget, and the VS Code setup.

| CR | Plan | Scope |
|---|---|---|
| CR-1 | [CR-1.md](CR-1.md) | The family chat service: Open WebUI, routing pipe, voice, images, documents, home-server deploy |
| CR-2 | [CR-2.md](CR-2.md) | Official Open WebUI image, unmodified; our files fetched from git on every Up; no image of our own |
| CR-3 | [CR-3.md](CR-3.md) | Document embeddings with Open WebUI's own model on the server, instead of OpenRouter |
| CR-4 | [CR-4.md](CR-4.md) | No integrations button: the Router picks image, web search, voice note or running code from each message |
