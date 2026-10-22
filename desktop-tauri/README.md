# Lycosa desktop app (Tauri)

The Lycosa desktop app for Windows, macOS and Linux: a React + TypeScript
interface in a Tauri 2 shell, with a Rust side that talks to controllers and
can run a whole controller on this computer (standalone mode). It is the only
Lycosa desktop app. The design is recorded as ADR-037 in the project's
decision log.

## Two ways to use it

- **Run Lycosa on this computer.** The app starts its bundled PostgreSQL 16,
  Qdrant and the Lycosa API (plus, if you switch it on, a local node), signs you
  in with a generated admin account, restarts a service that crashes (up to
  three times in ten minutes), and stops everything cleanly when it closes.
  It needs no Python, Node, Docker or terminal.
- **Connect to a controller.** Any Lycosa controller on your network (for
  example the Docker install). Nothing is started locally.

You can keep several controller profiles and switch between them. The local
one is called *This computer*.

## What runs where

| Piece | Where | Notes |
|---|---|---|
| UI | webview (React) | Gets no shell, file, network or opener permission: 32 typed commands only (`src-tauri/capabilities/main.json`). |
| Controller requests | Rust (`src-tauri/src/controller`) | Only `/api/v1/*` and `/healthz` of the active profile. The token is added in Rust. Redirects are never followed. API keys and Phantom input need HTTPS or a loopback controller. |
| Session tokens | OS keychain | Windows Credential Manager, macOS Keychain or Secret Service. Without a keychain they stay in memory, and the header says so. |
| Live events, chat streaming, LAN scan, OpenRouter sign-in | Rust | WebSocket with a 5 s reconnect; SSE parsing; mDNS (`mdns-sd`); a one-shot 127.0.0.1 listener. |
| Standalone services | Rust supervisor (`src-tauri/src/supervisor`) | PostgreSQL → Qdrant → migrations → admin seed → API → optional agent. |

### Standalone mode in detail

- **Network:**
  - Every service listens on 127.0.0.1, on free ports that are reused from
    one launch to the next.
  - **Network-controller mode** (*This computer → Network*) makes only the API
    listen on every interface, on a fixed port (8000 by default), so agents
    and other desktop apps can connect. PostgreSQL and Qdrant stay on
    loopback.
  - Every API request needs a sign-in or an API key in both modes; loopback is
    never treated as authentication.
- **Secrets:**
  - The PostgreSQL password, Qdrant API key, JWT secret and admin password
    are generated once into `secrets.json` (owner-only on Unix) and passed to
    the services through their environment.
  - The backend generates its own provider-credential key in `backend/`.
  - Log lines are redacted before they are stored or shown.
- **Shutdown:**
  - Closing the app stops the services: the API and Qdrant are terminated,
    then PostgreSQL stops cleanly with `pg_ctl stop -m fast`.
  - A run, task or ingestion the API was executing at that moment is failed
    by the controller's lease reaper on the next start (ADR-034…036).
  - If the app crashes: on Windows and Linux its children die with it. On
    macOS they are cleaned up at the next start. A PostgreSQL server left
    running is stopped and restarted under supervision.

### Your data

Everything lives in the app's local data folder:

| OS | Folder |
|---|---|
| Windows | `%LOCALAPPDATA%\com.lycosa.desktop` |
| macOS | `~/Library/Application Support/com.lycosa.desktop` |
| Linux | `~/.local/share/com.lycosa.desktop` |

It holds:

- `postgres/` — the database cluster;
- `qdrant/` — the vectors;
- `backend/` — the provider-credential key;
- `secrets.json` and `standalone.json`;
- `logs/`;
- `backups/`.

Updates and uninstalls leave it in place. The Windows uninstaller deletes it
only if you tick *Delete the application data*. Saved controller profiles are
in the app config folder, and their tokens are in the OS keychain.

**Backup and recovery:**

- *This computer → Back up now* writes a `pg_dump` of the database into
  `backups/`; the newest five are kept.
- One is taken automatically whenever a new app version is about to migrate
  the database.
- For a complete backup, stop the controller and copy the whole data folder.
  The credential key and `secrets.json` must stay with the database:
  without them, stored provider keys cannot be decrypted.
- To restore, stop the controller, put the folder back, and start it.

**Upgrades:**

- Installing a newer version keeps the data folder. The next start backs up
  the database, then runs the new migrations.
- The bundled PostgreSQL major version is fixed at 16. A cluster made by a
  different major is refused with a clear message rather than touched, so a
  future major upgrade ships its own migration step.

## Features

UI code is under `src/`, Rust code under `src-tauri/src/`.

| Area | Code | Notes |
|---|---|---|
| Connection setup, multiple profiles, switch, sign in/out, remove a controller | `features/setup`, `features/auth`, `state/session.tsx`, `controller/profiles.rs` | The session is re-validated after any 401 |
| Secure credential storage | `controller/profiles.rs` (`keyring`) | Profile names and URLs in `profiles.json`, one token per profile in the keychain; tokens never reach the webview |
| Live events + reconnect, alert toasts, node-list refresh | `controller/events.rs`, `state/events.ts`, `features/shell` | |
| Nodes list, status, usage | `features/nodes/NodesScreen.tsx` | |
| Node graph (star topology, drag, pan, zoom, full screen) | `NodesGraph.tsx`, `graphSim.ts` | |
| Node detail: usage, GPU, hardware, role, model recommendations + install, delete | `NodeDetail.tsx` | |
| Add node (mint key, per-OS commands, localhost → LAN IP) | `AddNodeDialog.tsx`, `netinfo.rs` | Hints when the local controller is not in network mode |
| LAN discovery (mDNS) incl. Windows virtual-adapter handling | `discovery.rs` (`mdns-sd`, per-interface) | |
| Tasks: provider, LLM routes, accounts, default-route preselection, results, recent + trace | `TasksScreen.tsx` | |
| Phantom (HTTPS/loopback only, no history, 60 s clear) | `PhantomDialog.tsx` (+ Rust transport check) | |
| Chat (SSE streaming, stop, target picker) | `features/chat`, `controller/http.rs` | |
| Workflows: create (templates), view, run, approve/reject, delete | `WorkflowsScreen.tsx`, `RunView.tsx` | |
| Knowledge: collections, upload, retrieval playground | `KnowledgeScreen.tsx` (file picker in Rust) | |
| Providers: accounts, capabilities, test, models, routing & fallback, prompt test | `features/llm` | |
| OpenRouter PKCE sign-in (loopback receiver) | `oauth.rs`, `ConnectAccountDialog.tsx` | |
| Claude subscription connect (ADR-032) | `ClaudeSubscriptionDialog.tsx` | |
| Admin: API keys + revoke, audit log, legacy provider keys | `features/admin` | |
| Light/dark theme without a flash | `theme-boot.ts`, `state/theme.tsx` | Stored in local storage (it is not a secret) |
| Run the controller on this computer | `supervisor/`, `features/local` | See *Standalone mode in detail* above |
| Acceptance tests against the real bundled services | `src-tauri/tests/standalone.rs`, `--smoke-test` | The UI is covered by fake-IPC flow tests; a live UI harness is on the backlog |

## Development

Prerequisites: Node 24, Rust (stable), Python 3.11.4+ to stage the runtime.
On Linux, also the WebKitGTK 4.1 development packages
(`libwebkit2gtk-4.1-dev libsoup-3.0-dev libjavascriptcoregtk-4.1-dev
libayatana-appindicator3-dev librsvg2-dev libdbus-1-dev`).

```bash
cd desktop-tauri
npm ci
npm test                     # vitest: unit + full-app flows on a fake IPC
npx tsc -b && npx eslint .   # types + lint
python scripts/stage_runtime.py   # pinned Python + PostgreSQL + Qdrant for this OS
npx tauri dev                # run the app

cd src-tauri
cargo fmt --check && cargo clippy --all-targets -- -D warnings
cargo test --lib             # unit tests
# the real bundled services, one test at a time:
LYCOSA_RUNTIME_DIR=$PWD/resources/runtime cargo test --test standalone -- --test-threads=1
# the end-to-end check the release runs on every installed package:
LYCOSA_RUNTIME_DIR=$PWD/resources/runtime cargo run -- --smoke-test --data-dir /tmp/lycosa-smoke
```

`stage_runtime.py` downloads only the versions pinned in
`runtime/manifest.json` and checks their sha256. It installs the
backend's and the agent's dependencies (wheels only, at the exact versions in
`runtime/constraints.txt`) and copies the backend and agent sources into
`src-tauri/resources/runtime/`, which is git-ignored. Without a staged
runtime the app still builds and works in remote mode, and *This computer*
says the runtime is not bundled.

`npx tauri build` produces the installers for the current OS. Release builds
run in CI on native runners (`.github/workflows/release.yml`), which also
install each package and run `--smoke-test` before publishing.
