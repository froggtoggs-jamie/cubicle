# Open Grok Bot

A local-first AI workspace for creating bot personas, chatting with models from any OpenAI-compatible server (OpenRouter, Ollama, LM Studio, llama.cpp, vLLM, or the OpenAI API), and keeping conversations on your machine. The interface is built with Next.js and React; the API is built with FastAPI and Python.

This is an independent open-source project and is not affiliated with xAI.

[Quick start](#quick-start) · [Configuration](#configuration) · [API](#api) · [Architecture](#architecture) · [Limitations](#limitations)

> **Status:** Prototype / active development. The project is designed for local experimentation and is not yet a production, multi-user agent platform.

## Related Projects

- [OpenRouter](https://openrouter.ai) — Default hosted provider; one key for hundreds of models behind an OpenAI-compatible API.
- [Ollama](https://ollama.com), [LM Studio](https://lmstudio.ai), [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server) — Local servers that expose the same API and work without a key.
- [MuAPI](https://muapi.ai) — The original provider, still supported as the legacy `muapi` option ([API reference](https://muapi.ai/docs/api-reference), [access keys](https://muapi.ai/access-keys)).
- [awesome-meta-muse-agent](https://github.com/Anil-matcha/awesome-meta-muse-agent) — copy-paste Muse agent briefs for practical workflows, connectors, and approval boundaries.
- [awesome-grok-bot](https://github.com/Anil-matcha/awesome-grok-bot) — curated bot templates for productivity, sales, marketing, operations, and personal workflows.
- [awesome-gpt-6-astra](https://github.com/Anil-matcha/awesome-gpt-6-astra) — evidence-backed model workflows, prompts, evaluations, and safety notes.

## What it does

- **Bot personas:** Create, edit, and switch between bots with their own role, system prompt, model, and visual identity.
- **Model picker:** Search the live model list reported by your server, or type any model ID. Models that advertise reasoning or vision support are tagged. The default model is `x-ai/grok-4.5` on OpenRouter.
- **SSE chat:** Send a message, persist it locally, and receive `turn.started`, `content.delta`, and `turn.completed` events over Server-Sent Events. Tokens are streamed from the model as they are generated.
- **Image attachments:** Upload JPEG, PNG, WEBP, GIF, or AVIF images. They are sent inline to vision-capable models as data URLs, so the image never leaves your machine except as part of the model request.
- **Markdown messages:** Render assistant replies as Markdown in the chat transcript.
- **Visible thinking:** Reasoning models' thinking (`reasoning_content` or `reasoning` in the stream) is shown live in a collapsible block above the reply. It is stored for display but never replayed to the model.
- **Voice dictation:** Use the browser's Web Speech API when the browser supports it.
- **Approved workspace tools:** Explicit `/workspace list`, `/workspace read`, and `/workspace write` requests pause for user approval, stay inside `WORKSPACE_ROOT`, and produce audit events.
- **Governed action gateway:** Workspace actions use a structured request/result contract, a deny-by-default registry, approval state, and redacted lifecycle audit records.
- **Settings drawer:** Choose the provider, base URL, API key, default model, optional `reasoning_effort`, Composio key, and local profile details.
- **Connector surface:** Browse a curated or live Composio app catalog, inspect connection status, and start OAuth authorization explicitly from Marketplace.
- **Read-only GitHub action:** After connecting GitHub, run an explicit issue lookup from chat and pass its structured result through the action gateway.
- **Approval-gated GitHub write:** Propose a GitHub issue from chat, inspect the repository/title/body-size preview, approve it, and receive a normalized issue result.
- **Audit trail:** Review local approval, workspace-tool, and connector events from the sidebar.
- **Computer provider surface:** Inspect a bot-scoped provider lifecycle, poll screen metadata, and run higher-risk provider actions through the same gateway used by tools.

## Quick start

### Requirements

- Node.js and npm
- Python and pip
- Either an OpenRouter API key, or a local OpenAI-compatible server such as Ollama, LM Studio, or llama.cpp

### 1. Clone the repository

```bash
git clone https://github.com/Anil-matcha/open-grok-bot.git
cd open-grok-bot
```

### 2. Start the FastAPI server

In a terminal window:

```bash
cd server
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# OpenRouter (default base URL):
export LLM_API_KEY="your_openrouter_api_key"

# ...or a local server, no key needed. For example Ollama:
# export LLM_BASE_URL="http://localhost:11434/v1"
# export DEFAULT_MODEL="llama3.2"

python run.py
```

The API starts at `http://127.0.0.1:8000`.

Everything above can also be set later from **App Settings → Model server** in the UI, which is the easier route when switching between a hosted provider and a local model. The provider presets there fill in the usual base URLs:

| Server | Base URL |
| --- | --- |
| OpenRouter | `https://openrouter.ai/api/v1` |
| Ollama | `http://localhost:11434/v1` |
| LM Studio | `http://localhost:1234/v1` |
| llama.cpp server | `http://localhost:8080/v1` |
| OpenAI | `https://api.openai.com/v1` |

Any server that implements `POST /chat/completions` with `stream: true` works. If it also implements `GET /models`, the picker lists its models; otherwise type the model ID into the picker's search box.

API documentation is available at:

- Swagger UI: `http://127.0.0.1:8000/docs`
- ReDoc: `http://127.0.0.1:8000/redoc`

### 3. Start the Next.js client

In a second terminal window:

```bash
cd client
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000).

You can also enter the provider key from **App Settings → Connections** after the UI loads. The environment variable is the server-side fallback.

The local server creates a mode-0600 session token in `DATA_DIR` and the browser establishes an HttpOnly session automatically when connecting from loopback. For a deployment accessed beyond the local machine, set `APP_AUTH_TOKEN` on the server and expose the same value to the client as `NEXT_PUBLIC_API_TOKEN` through the deployment environment.

### Optional Docker computer runtime

The default provider is the deterministic local adapter. To enable real browser, terminal, file, input, and screenshot operations, build the pinned runtime image and opt in to the Docker provider:

```bash
docker build -t open-grok-bot-computer:1.62.1 ./runtime
export COMPUTER_PROVIDER=docker
export COMPUTER_DOCKER_IMAGE=open-grok-bot-computer:1.62.1
```

The Docker daemon must be running before starting the API. Each bot gets a separate container, a separate workspace under `DATA_DIR/computers`, an ephemeral loopback-only port, and an internal runtime token. The container root is read-only, capabilities are dropped, and CPU, memory, process, and shared-memory limits are applied. The current runtime provides browser screenshots and approved operations; human takeover and a desktop/VNC surface remain follow-up work.

This is a local development runtime, not a hardened hostile-web sandbox. Follow the [official Playwright Docker guidance](https://playwright.dev/docs/docker) and do not send untrusted websites or credentials through it until network egress, image provenance, and stronger sandboxing are reviewed for your deployment.

## Configuration

The client defaults to `http://127.0.0.1:8000/api/v1`. Set `NEXT_PUBLIC_API_URL` if the API runs elsewhere:

```bash
NEXT_PUBLIC_API_URL="http://127.0.0.1:8000/api/v1"
```

The server reads these variables from the environment:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_PROVIDER` | `openai_compatible` | `openai_compatible` or the legacy `muapi` |
| `LLM_API_KEY` | empty | Provider credential used when no key is saved in local settings. Optional for local servers |
| `LLM_BASE_URL` | per provider | API base URL. Defaults to `https://openrouter.ai/api/v1` for `openai_compatible` and `https://api.muapi.ai/api/v1` for `muapi` |
| `LLM_REASONING_EFFORT` | empty | When set it is sent verbatim as `reasoning_effort`. Common values are `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`; which ones work depends on the server and model. Leave empty to let the server use its default |
| `MUAPI_API_KEY`, `MUAPI_BASE_URL` | empty | Legacy names. When `LLM_PROVIDER` is unset and `MUAPI_API_KEY` is present, the provider defaults to `muapi` and these values are used |
| `COMPOSIO_API_KEY` | empty | Optional connector credential used when no key is saved in local settings |
| `DEFAULT_MODEL` | `x-ai/grok-4.5` (`grok-4-5` for MUAPI) | Initial model used for new settings and bots |
| `DATA_DIR` | per-user hidden app directory | SQLite database, migration copies, and local key location |
| `APP_ENCRYPTION_KEY` | generated mode-0600 key in `DATA_DIR` | Optional Fernet key for encrypted provider credentials |
| `APP_AUTH_TOKEN` | generated mode-0600 token in `DATA_DIR` | Bearer token for non-loopback API access |
| `AUTH_SESSION_MAX_AGE` | `86400` | Session-cookie lifetime in seconds |
| `AUTH_COOKIE_SECURE` | `0` | Set to `1` when serving over HTTPS |
| `CORS_ORIGINS` | localhost and loopback client origins | Comma-separated browser origins allowed by the API |
| `COMPUTER_PROVIDER` | `fake` | Computer adapter: `fake` or `docker` |
| `COMPUTER_DOCKER_IMAGE` | `open-grok-bot-computer:1.62.1` | Pinned local runtime image |
| `COMPUTER_DOCKER_WORKSPACE_ROOT` | `DATA_DIR/computers` | Root for per-bot runtime workspaces |
| `COMPUTER_DOCKER_CPU_LIMIT` | `2.0` | Docker CPU limit per computer |
| `COMPUTER_DOCKER_MEMORY_LIMIT` | `2g` | Docker memory limit per computer |
| `COMPUTER_DOCKER_PIDS_LIMIT` | `512` | Maximum processes per computer |
| `COMPUTER_DOCKER_START_TIMEOUT` | `20` | Runtime readiness timeout in seconds |
| `COMPUTER_DOCKER_COMMAND_TIMEOUT` | `30` | Docker/driver operation timeout in seconds |
| `WORKSPACE_ROOT` | repository root | Maximum directory that approved workspace tools can access |
| `WORKSPACE_MAX_FILE_BYTES` | `131072` | Read/write size limit for workspace files |
| `APPROVAL_TIMEOUT_SECONDS` | `120` | How long a pending approval remains open |
| `HOST` | `127.0.0.1` | FastAPI bind address |
| `PORT` | `8000` | FastAPI port |

For `openai_compatible`, chat requests go to `{LLM_BASE_URL}/chat/completions` and the model list comes from `{LLM_BASE_URL}/models`. For `muapi`, the selected model ID is appended to the base URL and long requests are polled at `/predictions/{id}/result`.

A key saved before the provider setting existed is migrated automatically: the database keeps using MUAPI with that key until you change the provider in App Settings.

## App surfaces

| Surface | Purpose |
| --- | --- |
| Chat | Bot roster, model selection, Markdown replies, image attachments, voice dictation, and streamed responses |
| Computer | Bot-scoped provider lifecycle, screen metadata polling, and governed computer actions |
| Marketplace | Searchable curated or live app catalog with explicit connect/disconnect actions |
| Audit trail | Recent approval, workspace-tool, and connector events persisted by the local API |
| App Settings | Local profile values, model server connection, default model, and reasoning effort |

## Architecture

```
Next.js client  ── HTTP + SSE ──▶  FastAPI server
                                     │
                 ┌───────────────────┼───────────────────┐
                 ▼                   ▼                   ▼
           SQLite + key store   LLM provider        Optional Composio
           state/settings/audit  OpenAI-compatible   connector endpoints
                                 or legacy MUAPI
                                     │
                                     ▼
                         Computer provider
                       fake or Docker/Playwright
                              + action gateway
```

The main code areas are:

| Path | Responsibility |
| --- | --- |
| `client/app/` | Next.js app entry points and global styles |
| `client/components/` | Dashboard, chat, model picker, settings, marketplace, audit, and preview surfaces |
| `client/lib/api.js` | HTTP and EventSource client functions |
| `server/app/main.py` | FastAPI app, CORS, router registration, and health route |
| `server/app/routers/` | Bots, chat, models, uploads, settings, approvals, connectors, and computers |
| `server/app/services/llm_service.py` | Provider dispatch for chat streaming and the model catalog |
| `server/app/services/llm_config.py` | Resolves provider, base URL, key, reasoning effort, and default model from env and saved settings |
| `server/app/services/openai_compatible_service.py` | Streaming `/chat/completions` client and `/models` normalisation for OpenAI-compatible servers |
| `server/app/services/muapi_service.py` | Legacy MUAPI requests, prediction polling, and the static MUAPI model registry |
| `server/app/services/storage_service.py` | SQLite persistence, legacy import, secrets, and default data |
| `server/app/services/database.py` | SQLite connection management and schema migrations |
| `server/app/services/secret_store.py` | Fernet encryption for provider credentials |
| `server/app/services/workspace_service.py` | Confined list/read/write workspace tools |
| `server/app/services/approval_broker.py` | Pending approval coordination and audit events |
| `server/app/services/action_gateway.py` | Registered action policy, approval handoff, execution, and lifecycle audit |
| `server/app/services/composio_service.py` | Server-side Composio MCP calls and normalized GitHub issue results |
| `server/app/services/connector_actions.py` | Explicit connector command parsing and gateway registration |
| `server/app/services/computer_provider.py` | Provider contract and deterministic local computer adapter |
| `server/app/services/docker_computer_provider.py` | Per-bot Docker lifecycle, token boundary, and runtime operations |
| `server/app/services/computer_actions.py` | Computer action definitions and gateway executors |
| `server/app/routers/computers.py` | Authenticated computer lifecycle, screen, and approval continuation routes |
| `runtime/` | Pinned Playwright container image and authenticated runtime driver |
| `server/app/schemas/contracts.py` | Pydantic request and response models |
| `server/tests/` | Focused workspace, approval, persistence, auth, and provider regression tests |

### Chat request flow

1. The client posts the user message to `/api/v1/chat/send`.
2. The server stores it in the local message store.
3. The client opens an EventSource connection to `/api/v1/chat/stream/{thread_id}`.
4. An explicit workspace or connector request becomes a structured action request and is checked against the deny-by-default gateway registry.
5. The gateway pauses the stream for approval when required, then executes the registered action.
6. The gateway emits normalized action lifecycle records; the client receives compatible tool events and the structured result.
7. The server sends the system prompt, full conversation history, and tool result to the configured provider as an OpenAI-style `messages` array (MUAPI receives a flattened prompt instead).
8. Provider output is forwarded as SSE deltas and the completed assistant message is persisted.

### Product direction

The prototype follows a local-first path: persistent bot personas and histories, explicit capabilities, user-visible approvals, provider flexibility, optional app connectors, and an opt-in isolated browser runtime. The next meaningful layers are durable memory and routines, voice input/output, human takeover, desktop/VNC presentation, background jobs, and authenticated multi-user deployment. They are intentionally documented as roadmap items rather than implied by the current UI.

### Approved workspace commands

These commands are intentionally explicit; arbitrary shell commands are not accepted:

```text
/workspace list [path]
/workspace read <path>
/workspace write <path>
file content on the next line
```

Paths must remain inside `WORKSPACE_ROOT`. Reads and writes are limited by `WORKSPACE_MAX_FILE_BYTES`, and every request/decision/result is recorded in the local audit store.

Connector actions are intentionally explicit:

```text
/connector github issues <owner>/<repo> [open|closed|all]
/connector github create-issue <owner>/<repo> <title>
optional body on the next line
```

The read action requires a configured Composio key and an active GitHub connection. The write action also requires an approval response. Results are limited to issue summaries; credentials and issue bodies are not copied into the action request preview or audit summary.

Computer actions are also explicit. Lifecycle controls use the authenticated `/computers/{bot_id}/...` routes and are recorded by the action gateway. Provider operations such as `terminal_execute`, `browser_navigate`, `files_list`, and `send_input` can be opened through `/computers/{bot_id}/actions`; higher-risk operations return a pending request until `/approvals/respond` allows them, then the matching `/actions/{request_id}/execute` route continues the action.

## API

All routes are prefixed with `/api/v1`.

| Method | Endpoint | Description |
| --- | --- | --- |
| GET | `/health` | Check server status and default model |
| GET | `/auth/status` | Report authentication state without exposing credentials |
| GET | `/auth/session` | Establish a local or bearer-backed browser session |
| POST | `/auth/login` | Exchange a configured bearer token for an HttpOnly session |
| POST | `/auth/logout` | Clear the current browser session |
| GET, POST | `/bots` | List or create bot personas |
| PUT, DELETE | `/bots/{bot_id}` | Update or delete a bot |
| GET | `/models` | Return the configured model catalog |
| GET | `/chat/history/{thread_id}` | Read a bot's message history |
| POST | `/chat/send` | Store a user message |
| GET | `/chat/stream/{thread_id}?model=...` | Stream a response over SSE |
| POST | `/upload` | Validate and upload an image attachment |
| GET, POST | `/settings` | Read public settings or save write-only credentials and app settings |
| POST | `/approvals/respond` | Submit an Allow/Deny approval response |
| GET | `/audit?limit=100` | Read recent approval, tool, and connector events |
| GET | `/connectors/catalog` | Return curated or Composio-backed connector cards |
| GET | `/connectors?services=...` | Check connector connection status |
| POST | `/connectors/{slug}/authorize` | Request an OAuth URL |
| DELETE | `/connectors/{slug}` | Disconnect a connector |
| GET | `/computers/{bot_id}` | Read provider status without creating a runtime |
| POST | `/computers/{bot_id}/create` | Create the bot-scoped provider runtime |
| POST | `/computers/{bot_id}/start` | Start the provider runtime |
| POST | `/computers/{bot_id}/pause` | Pause the provider runtime |
| POST | `/computers/{bot_id}/stop` | Stop the provider runtime |
| POST | `/computers/{bot_id}/reset` | Reset provider state and generation |
| GET | `/computers/{bot_id}/health` | Read provider health through the gateway |
| GET | `/computers/{bot_id}/screenshot` | Read the current screen-state metadata |
| POST | `/computers/{bot_id}/actions` | Open a provider operation, returning 202 when approval is required |
| POST | `/computers/{bot_id}/actions/{request_id}/execute` | Continue an approved provider operation |

Try the health route after starting the server:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

## Local data and secrets

On first start, the server creates a SQLite database under the per-user data directory defined in `server/app/config.py`. Bots, messages, settings, approvals, and audit events survive restarts, with schema migrations and a local owner identity tracked in the database. Existing JSON files are imported once and retained as migration copies; credential fields in the old settings file are scrubbed after import.

For local development:

- API routes require authentication. Loopback browser clients receive a session automatically; direct API clients can read `.auth-token` from `DATA_DIR` and send `Authorization: Bearer <token>`.
- For non-loopback access, set `APP_AUTH_TOKEN` explicitly and configure `NEXT_PUBLIC_API_TOKEN` or call `/auth/login` before using protected routes. Do not expose the generated token through logs or source control.
- Provider credentials are encrypted at rest with a mode-0600 Fernet key in `DATA_DIR`. Set `APP_ENCRYPTION_KEY` when the key must be supplied by deployment secrets or shared across restarts and hosts.
- Settings responses never return provider credentials. Enter a new value to replace a stored key, or leave it blank to keep the current one.
- Back up the SQLite database and encryption key together. If the key is lost, encrypted credentials must be entered again.
- Keep `CORS_ORIGINS` narrow and use HTTPS plus `AUTH_COOKIE_SECURE=1` outside local development.
- Never commit API keys, local settings, transcripts, or generated environment files.
- The repository ignores local SQLite files, encryption keys, `.env` files, virtual environments, caches, and build output.

## Limitations

The following surfaces are present but should not be mistaken for completed infrastructure:

- **Single local owner:** Authentication, bearer validation, and owner-scoped rows are present, but user provisioning, roles beyond the local owner, and multi-user grants are still pending.
- **Single-user local storage:** SQLite improves restart durability, but multi-user provisioning, backups, and multi-instance coordination are still pending.
- **Docker runtime is opt-in:** The Docker/Playwright provider and image are included, but live container startup depends on a running Docker daemon and a locally built image. The current runtime has browser screenshots and controlled operations, not a full desktop or VNC session.
- **No durable memory or routines:** Conversations persist, but there is no separate memory store, scheduled routine engine, or background worker yet.
- **No voice or multi-client apps:** Voice, desktop, and mobile clients are not included in this repository.
- **Workspace and computer tools are intentionally narrow:** Workspace commands support confined file listing, reads, and writes. Docker computer operations run in a bot-scoped container and require approval for browser navigation, terminal commands, and input; human takeover and unrestricted desktop control are not implemented.
- **Connector actions are intentionally narrow:** Chat currently exposes only GitHub issue listing and approval-gated issue creation. Dynamic tool discovery, arbitrary connector calls, and other connector writes remain roadmap work.
- **Provider streaming is adapter-level:** Depending on the provider response, the service may receive a completed result and emit it to the UI in small deltas.
- **No CI workflow is included yet:** Focused workspace, approval, auth, persistence, and provider tests are present, but broader runtime integration and browser tests remain to be added.

## Development

Available client scripts:

```bash
cd client
npm run dev       # Start the development server
npm run build     # Create a production build
npm run start     # Serve the production build
npm run lint      # Run the configured Next.js lint command
```

Run the focused backend tests with:

```bash
DATA_DIR=/tmp/open-grok-bot-test-data PYTHONPATH=server python -m unittest discover -s server/tests -v
```

When changing an API contract, update the Pydantic schema, router, client helper, tests, and this README together.

## Troubleshooting

### The UI says the API is offline

Confirm that the FastAPI server is running on port 8000, then check:

```bash
curl http://127.0.0.1:8000/api/v1/health
```

If the server runs on another host or port, set `NEXT_PUBLIC_API_URL` before starting the client.

If protected API calls return `401`, confirm the browser origin is listed in `CORS_ORIGINS`. For non-loopback deployments, set `NEXT_PUBLIC_API_TOKEN` when building the client or call `/api/v1/auth/login` first.

### The Docker computer stays unavailable

Confirm that the daemon is running, the runtime image was built, and the API uses the Docker adapter:

```bash
docker info
docker image inspect open-grok-bot-computer:1.62.1
echo "$COMPUTER_PROVIDER"
```

The API intentionally reports a failed Docker start instead of silently executing the task in the fake adapter. Keep `COMPUTER_PROVIDER=fake` when Docker is not available.

### The chat returns a provider error

The error text from the server is shown in the chat as the reply. Common causes:

- **HTTP 401 / 403:** the key is missing or wrong. Open App Settings → Model server and save a key.
- **HTTP 404 on `/chat/completions`:** the base URL is wrong. For Ollama, LM Studio and llama.cpp it must end in `/v1`.
- **Unknown or unloaded model:** the model ID is not one the server offers. Use the picker's refresh button to reload the list, or load the model in your local server first.
- **Unrecognized argument `reasoning_effort`:** the server or model does not accept it. Set Reasoning effort back to "Not sent" in App Settings.
- **Could not reach ...:** the server is not running or is not listening on that host and port.

### The model picker is empty or shows only the default model

The server did not answer `GET /models`. The picker still accepts a typed model ID, so search for the ID you want and choose "Use ... as the model ID".

### Images are ignored by the model

Images are sent as OpenAI-style `image_url` parts. The selected model must support image input; on OpenRouter such models are tagged "Vision" in the picker. For local servers choose a vision model such as a LLaVA or Qwen-VL variant.

## Contributing

Focused issues and pull requests are welcome. Before opening a change:

- Keep setup and behavior documentation synchronized with the code.
- Do not include secrets, personal data, or local transcripts.
- Explain changes to provider behavior, API contracts, or persistence.
- Run the client checks that apply to your change.

## License

This repository does not currently include a license file. Add an explicit license before distributing it as a reusable package.
