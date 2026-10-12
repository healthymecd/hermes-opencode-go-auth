# ⚚ hermes-opencode-go — Use your OpenCode Go subscription as a Hermes provider

A self-contained [Hermes Agent](https://hermes-agent.nousresearch.com) model-provider
plugin that signs you into your **OpenCode Go / Go Plus** subscription with the same
**Sign in with OpenCode** device flow the OpenCode CLI uses — then serves it to Hermes
as a first-class inference provider:

```
❯ hermes chat --provider opencode-go -m glm-5.3-flash -q "hello"
Hermes Agent running GLM (by Z.ai) via the opencode-go provider.
```

- ✅ Browser device sign-in (`hermes auth add opencode-go`) — no API key copy/pasting
- ✅ OAuth pair auto-refresh (on expiry and on 401, pool-locked, single-rotation-safe)
- ✅ Live model catalog (38 models: `glm-5.3-flash`, `kimi-k2.6`, `minimax-m3`, `deepseek-v4-pro`, `qwen3.8-max`, …)
- ✅ Carries the bundled Go relay wire quirks (GLM-5.2 / Kimi K2 / DeepSeek thinking
  knobs, MiMo max-tokens cap, strict tool-content downgrade)
- ✅ Static API-key mode (`OPENCODE_GO_API_KEY`) still supported

---

## Requirements

| | |
|---|---|
| Hermes Agent | any 2026.9+ release (`hermes --version`) — macOS, Linux or Windows/WSL |
| A paid OpenCode Go subscription | sign in via your OpenCode account (GitHub / Google); device approval is one click |
| Python deps | none extra — uses `httpx`, which Hermes core already ships |

Third-party model-provider plugins for built-in provider names are wired by Hermes:
`--provider opencode-go`, `/model`, `hermes model`, and runtime resolution all key on
the profile this plugin re-registers.

## Package contents

```
hermes-opencode-go-auth/
├── __init__.py          the plugin — profile + device auth + refresh hooks
├── plugin.yaml          model-provider manifest (kind: model-provider)
├── PLUGIN_NOTES.md      plugin internals / architecture notes
├── install.sh           manual installer (copies the plugin into your Hermes home)
├── README.md            ← you are here
└── LICENSE              MIT
```

## Install

### Option A — install with Hermes itself

```sh
hermes plugins install healthymecd/hermes-opencode-go-auth
```

The plugin sits at the repo ROOT — exactly what `hermes plugins install` needs
for a `kind: model-provider` package — so the command above is all it takes
(discovery loads it; no `hermes plugins enable` step needed).

### Option B — installer script

```sh
git clone git@github.com:healthymecd/hermes-opencode-go-auth.git
cd hermes-opencode-go-auth
./install.sh
```

### Option C — manual drop-in

Model-provider plugins load from `$HERMES_HOME/plugins/model-providers/<name>/`
(default `HERMES_HOME` is `~/.hermes`):

```sh
mkdir -p ~/.hermes/plugins/model-providers
cp -R opencode-go ~/.hermes/plugins/model-providers/
```

(Hermes runs as an unusual account or in a container? Use the real home instead:
`cp -R __init__.py plugin.yaml "$HERMES_HOME/plugins/model-providers/opencode-go/"`.)

### Option D — git checkout

If you cloned a repo containing this directory into
`~/.hermes/plugins/<name>` (the flat location `hermes plugins install` clones into),
it works there too: the manifest declares `kind: model-provider`, so provider
discovery accepts it — no `hermes plugins enable` needed.

No restart is strictly required for **CLI sessions started after the copy**, but
if Hermes is mid-session: just relaunch (discovery happens at first provider
lookup). Running gateways should be restarted to pick it up cleanly.

## Sign in

```sh
hermes auth add opencode-go
```

1. Hermes prints a short code and opens
   `https://opencode.ai/console/device?user_code=<CODE>` in your browser.
2. Sign in with GitHub/Google (if requested), enter the code, approve the device.
3. The OAuth pair is stored in the standard Hermes credential pool
   (`~/.hermes/auth.json`) — labeled with your account email.

Headless / SSH: the URL and code are printed; open them anywhere, then the
terminal continues automatically (polls until you approve or the code expires —
10 minutes).

## Use it

```sh
hermes --provider opencode-go -m glm-5.3-flash
hermes chat --provider opencode-go -m glm-5.3-flash -q "quick question"
```

Inside a session: `/model --provider opencode-go`. The catalog is fetched live;
popular ids: `glm-5.3-flash`, `glm-5.3`, `kimi-k2.6`, `kimi-k3`, `minimax-m3`,
`deepseek-v4-pro`, `qwen3.8-max`, `claude-haiku-5-5`.

## Command reference

| Command | What it does |
|---|---|
| `hermes auth add opencode-go` | device sign-in (stores OAuth pair) |
| `hermes auth status opencode-go` | account email, org, token expiry, credential health |
| `hermes auth refresh opencode-go` | rotate the OAuth pair by hand (optional; mostly automatic) |
| `hermes auth logout opencode-go` | remove the stored credential |

## How it works (two credential modes)

| Mode | Credential | Endpoint |
|---|---|---|
| Device sign-in (subscription) | OpenCode Console OAuth pair (`st_…`/`rt_…`) | `https://opencode.ai/inference/go/openai/v1` |
| Static key | `OPENCODE_GO_API_KEY` | `https://opencode.ai/zen/go/v1` |

- Subscription routes live per-credential: each pooled row carries its own
  `base_url`, and `resolve_runtime_provider` honors it — sign-in mode never sends
  console session tokens to the `zen/go` key relay (that combination 401s).
- The relay requires OpenCode's `x-opencode-session` header; Hermes already sends
  it for any `opencode.ai` target (built-in conversation-affinity machinery).
- Rotation runs under the shared `auth.json` lock; if two Hermes processes race,
  the second adopts the fresh pair instead of double-consuming the grant.
- `hermes usage` windows currently report only in static-key mode
  (the `zen/go/v1/usage` endpoint doesn't accept session tokens).
- A wrong credential (e.g. a session `st_…` token pasted into `OPENCODE_GO_API_KEY`
  in `~/.hermes/.env`) poisons pool entries and produces 401 loops — lines
  like `base_url=https://opencode.ai/zen/v1` inside `~/.hermes/logs/agent.log`
  are the giveaway. Comment those lines out and re-run `hermes auth add opencode-go`.

## Troubleshooting

| Symptom | Meaning / fix |
|---|---|
| `hermes auth add` opens the browser but nothing happens in the terminal | approve the device in the browser; the terminal polls for 10 min. Code input field sits under "Enter code" on the page. |
| 401 `Invalid API key` gone after re-login but 401s persist | pool rows got benched `exhausted` — run `hermes auth refresh opencode-go` once, or `hermes auth logout opencode-go` then `hermes auth add opencode-go`. |
| `Insufficient account funds` | your **OpenCode Console Go credit/balance** is out — top up at opencode.ai/console. (Seen through the provider family, not this plugin.) |
| Provider missing from `/model` picker | the plugin lists once a way in exists (device row present or `OPENCODE_GO_API_KEY` set). Direct `--provider opencode-go` still works always. |
| Multi-org account picks the wrong org | sign-in selects your first org (alphabetical, like OpenCode CLI). Use a static org-scoped `OPENCODE_GO_API_KEY` for a specific org. |
| `export OPENCODE_GO_API_KEY=…` ignored | static keys respond from a sibling entry; `hermes auth status opencode-go` shows both. Keep whichever you want; remove the other with `hermes auth logout` or delete the env seed. |

## Uninstall

```sh
hermes auth logout opencode-go                  # optional: remove the credential
rm -rf ~/.hermes/plugins/model-providers/opencode-go
```

## Security

- Tokens live in `~/.hermes/auth.json` (600 perms, standard Hermes pool) — never
  logged by the plugin; `auth status` shows expiry, not secrets.
- The device flow uses only `https://opencode.ai` endpoints (`http://` hosts are
  rejected except loopback).
- Refresh rotation is concurrency-safe: single-use refresh tokens are never
  double-spent (peer rotation is adopted under the lock).
- Attribution headers match the bundled profile: `HTTP-Referer:
  https://hermes-agent.nousresearch.com`, `X-Title: Hermes Agent`.

## License

MIT — see `LICENSE`.
