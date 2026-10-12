# opencode-go (Hermes model-provider plugin)

Use your **OpenCode Go / Go Plus subscription** as a Hermes inference provider.
This plugin overrides Hermes' bundled `opencode-go` profile and adds
**Sign in with OpenCode** — the same device-code flow the OpenCode CLI uses
(client `opencode-cli`, issuer `https://opencode.ai/console`) — plus OAuth token
auto-refresh.

Two credential modes are supported:

| Mode | Credential | Endpoint |
|---|---|---|
| Device sign-in (subscription) | OpenCode Console OAuth pair (`st_…` / `rt_…`) | `https://opencode.ai/inference/go/openai/v1` |
| Static key | `OPENCODE_GO_API_KEY` (Console → Create API Key) | `https://opencode.ai/zen/go/v1` |

## Install / load

The directory `~/.hermes/plugins/model-providers/opencode-go/` is auto-discovered
by Hermes provider discovery (user plugins override the bundled `opencode-go`
profile — no enablement step needed). Restart Hermes after editing.

## Sign in

```
hermes auth add opencode-go
```

1. Hermes prints a short code and opens `https://opencode.ai/console/device` in
   your browser (sign in with GitHub/Google if needed).
2. Enter the code, approve the device.
3. Hermes stores the OAuth pair in the standard credential pool
   (`~/.hermes/auth.json`) and names the credential your account email.

Add flags: `--no-browser` (headless — open the printed URL manually),
`--server https://opencode.ai/console` (self-hosted console override).

## Use it

```
hermes --provider opencode-go -m glm-5.3-flash
# or inside a session:
/model --provider opencode-go
```

Model catalog is fetched live from `https://opencode.ai/zen/go/v1/models`
(e.g. `glm-5.3-flash`, `glm-5.3`, `kimi-k2.6`, `minimax-m3`, `deepseek-v4-pro`,
`qwen3.8-max`, ...).

```
hermes auth status opencode-go   # account, org, token expiry
hermes auth refresh opencode-go  # rotate the OAuth pair by hand
hermes auth logout opencode-go   # forget the credential
```

Device-signed-in sessions route through the per-org inference surface with
Hermes' built-in OpenCode session-affinity header (`x-opencode-session`) — the
same header the OpenCode CLI sends. Go plan usage windows (`hermes usage`)
currently require the static-key mode.

## Token rotation & recovery

- Access tokens are rotated automatically through the credential pool before
  expiry, and on 401 during inference (`refresh_credential` hook, max 2 refreshes
  per row per session).
- If the refresh grant is dead (`invalid_grant`), the row is benched DEAD and
  Hermes tells you to `hermes auth add opencode-go` again.
- Rotation runs under the shared `auth.json` lock; if another Hermes process
  already rotated the pair, the fresh pair is adopted.

## Static API-key mode (still supported)

Alternatively, `export OPENCODE_GO_API_KEY=...` (from the Console's *Create API
Key*) keeps working: the profile keeps that env var registered, and `auth
status` reports it. Device sign-in and the static key are independent
credentials — pick one.

## Multi-org accounts

Sign-in selects your first organization (alphabetically, like the OpenCode
CLI). Requests omit the `x-org-id` header; if your account belongs to several
orgs and you need a specific one, use an org-scoped static
`OPENCODE_GO_API_KEY` instead.
