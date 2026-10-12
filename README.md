# ⚚ hermes-opencode-go-auth

Use your **OpenCode Go / Go Plus subscription** as a Hermes inference provider.

A [Hermes Agent](https://hermes-agent.nousresearch.com) model-provider plugin that
signs you in with the same **Sign in with OpenCode** device flow the OpenCode CLI
uses, then serves the subscription to Hermes:

```
❯ hermes chat --provider opencode-go -m glm-5.3-flash
Hermes Agent running GLM (by Z.ai) via the opencode-go provider.
```

OAuth tokens are rotated automatically (on expiry and on 401). Model options are
fetched live: `glm-5.3-flash`, `glm-5.3`, `kimi-k2.6`, `minimax-m3`,
`deepseek-v4-pro`, `qwen3.8-max`, and more.

Requires: Hermes Agent (2026.9+) and an OpenCode Go subscription. No extra Python
dependencies.

## 1. Install

```sh
hermes plugins install healthymecd/hermes-opencode-go-auth
```

Check it loaded:

```sh
hermes plugins list          # should show: opencode-go-provider ✓
```

## 2. Login

```sh
hermes auth add opencode-go
```

- A short code prints in the terminal and your browser opens
  `console.opencode.ai/device`.
- Sign in with GitHub/Google (if the page asks), enter the code, approve the
  device.
- The terminal prints `Signed in to OpenCode Go as …` — done.

## 3. Verify and use

```sh
hermes auth status opencode-go
hermes chat --provider opencode-go -m glm-5.3-flash -q "ping"
```

Inside any session: `/model --provider opencode-go`.

| Command | What it does |
|---|---|
| `hermes auth add opencode-go` | sign in (device flow) |
| `hermes auth status opencode-go` | account email, org, token expiry |
| `hermes auth refresh opencode-go` | rotate tokens by hand (generally automatic) |
| `hermes auth logout opencode-go` | forget the credential |

## Notes

- Tokens live in the standard Hermes credential pool (`~/.hermes/auth.json`);
  nothing is logged by the plugin.
- Static-key mode still works for hands-free automation: set `OPENCODE_GO_API_KEY`
  (from the Console's *Create API Key*) — it routes through
  `https://opencode.ai/zen/go/v1` independently of device sign-in.
- Do **not** paste a session token (`st_…`) into `OPENCODE_GO_API_KEY` — that is
  not an API key and produces 401 `Invalid API key` loops. If you ever hit those,
  remove the exact `st_` value from `~/.hermes/.env` and start a fresh terminal.
- `hermes usage` windows only report in the static-key mode.
- Multi-org accounts: sign-in picks your first org automatically; use a static
  org-scoped API key to force a specific one.

## Remove / uninstall

```sh
hermes auth logout opencode-go
hermes plugins remove opencode-go-provider
rm -rf ~/.hermes/plugins/opencode-go-provider
rm -rf ~/.hermes/plugins/model-providers/opencode-go
```

(Step 1 removes the credential, step 2 removes the installed plugin, steps 3–4
catch any manual drop-in copies. Skip `auth logout` if you plan to reinstall and
keep the same sign-in.)

## License

MIT — see `LICENSE`.
