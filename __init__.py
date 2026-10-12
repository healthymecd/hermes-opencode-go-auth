"""OpenCode Go — subscription sign-in provider for Hermes.

A user-level override of the bundled ``opencode-go`` model-provider profile that
adds first-class account authentication:

- ``hermes auth add opencode-go``     — Sign in with OpenCode (device-code flow
  against the OpenCode Console, the same flow the OpenCode CLI uses: client
  ``opencode-cli``, console ``{server}/auth/device/code`` +
  ``{server}/auth/device/token``).
- ``hermes auth status opencode-go``  — account email, organization, token expiry,
  and Go plan usage windows.
- ``hermes auth logout opencode-go``  — forget the stored credential.
- ``hermes auth refresh opencode-go`` — rotate the OAuth pair (also automatic on
  401 via the credential pool).

The rotated subscription credential lives in the standard Hermes credential pool
(``~/.hermes/auth.json``); inference goes to ``https://opencode.ai/zen/go/v1``
with ``Authorization: Bearer <access token>``. A static ``OPENCODE_GO_API_KEY``
env var (from ``opencode auth login opencode-go --method key`` or the Console's
"Create API Key") keeps working and is reported by ``auth status``.
"""

import logging
import os
import time
import uuid
import webbrowser
from datetime import UTC, datetime
from typing import Any

import httpx

from providers import register_provider
from providers.base import ProviderProfile

logger = logging.getLogger(__name__)

# CONTRACT (plugins during provider discovery): only `providers` may be imported at module
# level. `agent.credential_pool` / `hermes_cli.auth_constants` are imported inside the hooks
# — discovery can run while `hermes_cli.auth` is still initializing (hermes_cli.config →
# plugin discovery), and a top-level import of that chain deadlocks the module.

PROVIDER_ID = "opencode-go"
# Same defaults the OpenCode CLI ships: console issuer + its CLI OAuth client.
DEFAULT_SERVER = "https://opencode.ai/console"
CLIENT_ID = "opencode-cli"
DEVICE_START_PATH = "/auth/device/code"
DEVICE_TOKEN_PATH = "/auth/device/token"
DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SESSION_TIMEOUT = 600.0  # /auth/device/code hands out expires_in=600 on the live service

# Attribution headers (same values the bundled opencode-zen profile sends).
_ATTRIBUTION_HEADERS = {
    "HTTP-Referer": "https://hermes-agent.nousresearch.com",
    "X-Title": "Hermes Agent",
}


def _flat_model_name(model: str | None) -> str:
    """Bare OpenCode model ID, tolerating aggregator prefixes."""
    return (model or "").strip().rsplit("/", 1)[-1].lower()

# Public catalog (no auth required; the per-org /inference surface has no /models).
CATALOG_URL = "https://opencode.ai/zen/go/v1/models"


_THINKING_CAPABLE_IDS: frozenset[str] = frozenset({"deepseek-reasoner", "deepseek-flash"})


def _is_deepseek_thinking_model(model: str | None) -> bool:
    m = _flat_model_name(model)
    return (m.startswith("deepseek-v") and not m.startswith("deepseek-v3")) or m in _THINKING_CAPABLE_IDS


def _is_glm_5_2_model(model: str | None) -> bool:
    """GLM-5.2 across alias spellings (glm-5.2 / glm-5-2 / glm-5p2)."""
    m = _flat_model_name(model)
    return any(token in m for token in ("glm-5.2", "glm-5-2", "glm-5p2"))


# ── HTTP helpers ──────────────────────────────────────────────────────────────


def _server_base(server: str | None) -> str:
    """Normalize the console base URL; https-only, no trailing slash."""
    from hermes_cli.auth_constants import AuthError

    raw = (server or DEFAULT_SERVER).strip().rstrip("/")
    if not raw.startswith(("https://", "http://localhost", "http://127.0.0.1")):
        raise AuthError(
            f"OpenCode server URL must be https:// (got {raw!r}); pass --server https://... to override",
            provider=PROVIDER_ID,
            code="bad_server",
        )
    return raw


def _device_start(client: httpx.Client, server: str) -> dict[str, Any]:
    response = client.post(f"{server}{DEVICE_START_PATH}", json={"client_id": CLIENT_ID, "supports_org_scope": True})
    response.raise_for_status()
    return response.json()


def _verification_url(server: str, start: dict[str, Any]) -> str:
    """console returns a path-relative verification URL — resolve against the server."""
    from urllib.parse import urljoin

    candidate = start.get("verification_uri_complete") or start.get("verification_uri") or ""
    return urljoin(server.rstrip("/") + "/", str(candidate))


def _device_token(client: httpx.Client, server: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = client.post(f"{server}{DEVICE_TOKEN_PATH}", json=payload)
    try:
        body = response.json()
    except ValueError:
        body = {}
    return response.status_code, body


def _poll_for_tokens(client: httpx.Client, server: str, device_code: str, interval: float, deadline: float) -> dict[str, Any]:
    """Poll the device grant, handling authorization_pending / slow_down."""
    from hermes_cli.auth_constants import AuthError

    interval = max(interval, 1.0)
    slow_down = 0.0
    while time.time() < deadline:
        time.sleep(interval + slow_down)
        slow_down = 0.0
        status, body = _device_token(
            client,
            server,
            {"grant_type": DEVICE_GRANT, "device_code": device_code, "client_id": CLIENT_ID},
        )
        if 200 <= status < 300 and body.get("access_token"):
            return body
        error = str(body.get("error") or "")
        if status == 400 and error == "authorization_pending":
            continue
        if status == 400 and error == "slow_down":
            slow_down = 5.0
            continue
        if error == "expired_token" or (status == 400 and error == "invalid_grant"):
            raise AuthError("The device code expired — start again with `hermes auth add opencode-go`",
                            provider=PROVIDER_ID, code="expired_token", relogin_required=True)
        if error in {"access_denied", "authorization_denied"}:
            raise AuthError("OpenCode device authorization was denied", provider=PROVIDER_ID, code="access_denied")
        raise AuthError(f"Device token exchange failed (HTTP {status}): {str(body)[:300]}",
                        provider=PROVIDER_ID, code="exchange_failed")
    raise AuthError("Timed out waiting for OpenCode device authorization", provider=PROVIDER_ID, code="timeout")


def _account_info(client: httpx.Client, server: str, access_token: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    user = client.get(f"{server}/api/user", headers={"Authorization": f"Bearer {access_token}"})
    user.raise_for_status()
    orgs = client.get(f"{server}/api/orgs", headers={"Authorization": f"Bearer {access_token}"})
    orgs.raise_for_status()
    return user.json() or {}, (orgs.json() if isinstance(orgs.json(), list) else [])


def run_device_sign_in(server: str | None = None, *, open_browser: bool = True,
                       refresh_token_entry: "PooledCredential | None" = None) -> dict[str, Any]:
    """Full Sign-in-with-OpenCode device flow. Returns account metadata + tokens."""
    from hermes_cli.auth_constants import AuthError
    from agent.credential_pool import PooledCredential  # noqa: F401  (annotation only)

    base = _server_base(server)
    with httpx.Client(timeout=20.0) as client:
        start = _device_start(client, base)
        expires_in = float(start.get("expires_in") or SESSION_TIMEOUT)
        deadline = time.time() + min(expires_in, SESSION_TIMEOUT)
        url = _verification_url(base, start)
        user_code = str(start.get("user_code") or "")
        print(f"\n  OpenCode: enter code {user_code} in your browser:")
        print(f"  {url}\n")
        if open_browser:
            webbrowser.open(url)
        tokens = _poll_for_tokens(client, base, str(start.get("device_code") or ""),
                                  float(start.get("interval") or 5), deadline)
        try:
            user, orgs = _account_info(client, base, str(tokens["access_token"]))
        except (httpx.HTTPError, ValueError) as exc:
            logger.debug("OpenCode account metadata fetch failed: %s", exc)
            user, orgs = {}, []
        org = None
        if orgs:
            orgs_sorted = sorted(orgs, key=lambda o: (str(o.get("name") or ""), str(o.get("id") or "")))
            requested = (getattr(refresh_token_entry, "extra", None) or {}).get("opencode_org_id") if refresh_token_entry else None
            org = next((o for o in orgs_sorted if str(o.get("id")) == str(requested)), orgs_sorted[0])
    return {
        "access_token": tokens["access_token"],
        "refresh_token": tokens.get("refresh_token"),
        "expires_in": int(tokens.get("expires_in") or 3600),
        "server": base,
        "account_id": user.get("id"),
        "email": user.get("email"),
        "org_id": (org or {}).get("id"),
        "org_name": (org or {}).get("name"),
    }


def _store_credential(result: dict[str, Any]) -> "PooledCredential":
    from agent.credential_pool import AUTH_TYPE_OAUTH, PooledCredential, load_pool

    pool = load_pool(PROVIDER_ID)
    email = str(result.get("email") or "OpenCode account")
    org_name = result.get("org_name")
    entry = PooledCredential(
        provider=PROVIDER_ID,
        id=uuid.uuid4().hex[:6],
        label=email if not org_name else f"{email} ({org_name})",
        auth_type=AUTH_TYPE_OAUTH,
        priority=0,
        source="manual:opencode_device",
        access_token=str(result["access_token"]),
        refresh_token=(result.get("refresh_token") or None),
        expires_at_ms=int(time.time() + result["expires_in"]) * 1000,
        # Subscription (console-session) credentials route through the per-org
        # inference surface; the static "zen/go/v1" relay only accepts Go API
        # keys (service-account surface). Per-row base_url — the pool resolves
        # runtime endpoints from credentials the credentials normalize
        # resolve_runtime_provider reads row.base_url.
        base_url="https://opencode.ai/inference/go/openai/v1",
        extra={
            "server": result.get("server") or DEFAULT_SERVER,
            "email": result.get("email"),
            "account_id": result.get("account_id"),
            "opencode_org_id": result.get("org_id"),
            "opencode_org_name": result.get("org_name"),
        },
    )
    pool.add_entry(entry)
    return entry


# ── Provider-owned auth hooks ─────────────────────────────────────────────────


def opencode_go_auth_handler(action: str, args: Any = None) -> bool:
    """`hermes auth add/status/logout opencode-go`."""
    from agent.credential_pool import load_pool
    from hermes_cli.auth_constants import AuthError

    if action == "add":
        server = getattr(args, "server", None)
        no_browser = bool(getattr(args, "no_browser", False))
        try:
            result = run_device_sign_in(server, open_browser=not no_browser)
        except AuthError as exc:
            print(f"OpenCode sign-in failed: {exc}")
            return True
        entry = _store_credential(result)
        org = f" · org {result['org_name']}" if result.get("org_name") else ""
        print(f"Signed in to OpenCode Go as {entry.label}{org}.")
        print("Use it with: hermes --provider opencode-go -m glm-5.3-flash")
        return True

    if action == "status":
        pool = load_pool(PROVIDER_ID)
        entries = [e for e in pool.entries() if e.last_status != "dead"]
        if not entries:
            print(f"{PROVIDER_ID}: not signed in. Run `hermes auth add opencode-go` "
                  f"(device sign-in) or set OPENCODE_GO_API_KEY.")
        for entry in entries:
            extra = entry.extra or {}
            state = "live" if entry.expires_at_ms and entry.expires_at_ms > time.time() * 1000 else "expired"
            label = f"{entry.label} · {state}"
            if entry.expires_at_ms:
                when = datetime.fromtimestamp(entry.expires_at_ms / 1000, UTC).strftime("%Y-%m-%d %H:%M")
                label += f", expires {when}"
            print(f"{PROVIDER_ID}: {label}")
            if extra.get("opencode_org_name"):
                print(f"  org: {extra['opencode_org_name']}")
        if not entries:
            from hermes_cli.config import get_env_value_prefer_dotenv
            if get_env_value_prefer_dotenv("OPENCODE_GO_API_KEY"):
                print(f"{PROVIDER_ID}: OPENCODE_GO_API_KEY is set (static key mode).")
        return True

    if action == "logout":
        pool = load_pool(PROVIDER_ID)
        removed = 0
        while pool.entries():  # remove_index() is 1-based and pops in place
            pool.remove_index(1)
            removed += 1
        print(f"{PROVIDER_ID}: signed out ({removed} credential(s) removed).")
        return True

    return False  # "refresh" and anything else: built-in pool handling + refresh_credential


def opencode_go_refresh_credential(entry: "PooledCredential"):
    """Rotate the OpenCode Console OAuth pair (single call, under the pool lock)."""
    from hermes_cli.auth_constants import AuthError

    server = _server_base((entry.extra or {}).get("server"))
    refresh_token = entry.refresh_token
    if not refresh_token:
        raise AuthError("No OpenCode refresh token stored — sign in again",
                        provider=PROVIDER_ID, relogin_required=True)
    with httpx.Client(timeout=20.0) as client:
        status, body = _device_token(client, server, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
        })
    if status == 400 and str(body.get("error") or "") == "invalid_grant":
        raise AuthError("OpenCode refresh grant is dead — sign in again: `hermes auth add opencode-go`",
                        provider=PROVIDER_ID, code="invalid_grant", relogin_required=True)
    if not (200 <= status < 300 and body.get("access_token")):
        raise AuthError(f"OpenCode token refresh failed (HTTP {status}): {str(body)[:200]}",
                        provider=PROVIDER_ID, code="refresh_failed")
    rotated: dict[str, Any] = {
        "access_token": str(body["access_token"]),
        "expires_in": int(body.get("expires_in") or 3600),
        "expires_at_ms": int(time.time() + int(body.get("expires_in") or 3600)) * 1000,
        "opencode_refreshed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    if body.get("refresh_token"):
        rotated["refresh_token"] = str(body["refresh_token"])
    return rotated


# ── Profile ───────────────────────────────────────────────────────────────────


class OpenCodeGoSigninProfile(ProviderProfile):
    """OpenCode Go — subscription sign-in + model-specific reasoning controls.

    Carries over every wire quirk of the bundled opencode-zen Go profile
    (GLM-5.2 / Kimi K2 / DeepSeek thinking knobs, MiMo max_tokens cap, Go plan
    usage windows, strict tool-content strings) and adds the OAuth auth hooks.
    """

    _MODEL_MAX_TOKENS: dict[str, int] = {"mimo-v2.5-pro": 131072}

    def get_max_tokens(self, model: str | None) -> int | None:
        cap = self._MODEL_MAX_TOKENS.get(_flat_model_name(model))
        return self.default_max_tokens if cap is None else cap

    def build_api_kwargs_extras(
        self, *, reasoning_config: dict | None = None, model: str | None = None, **context
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        from agent import reasoning_effort as re_

        if _is_glm_5_2_model(model):
            effort = re_.requested_effort(reasoning_config)
            if effort is None or effort == "none":
                return {}, {}
            clamped = re_.clamp_effort(effort, re_.GLM52_EFFORTS, re_.GLM52_OVERRIDES)
            return {}, {"reasoning_effort": clamped if clamped in re_.GLM52_EFFORTS else "high"}
        if _flat_model_name(model).startswith("kimi-k2"):
            if not isinstance(reasoning_config, dict):
                return {}, {}
            return re_.thinking_toggle_extras(reasoning_config, re_.KIMI_K2_EFFORTS)
        if _is_deepseek_thinking_model(model):
            return re_.thinking_toggle_extras(reasoning_config, re_.DEEPSEEK_V4_EFFORTS, re_.DEEPSEEK_V4_OVERRIDES)
        return {}, {}

    def fetch_models(self, *, api_key: str | None = None, base_url: str | None = None, timeout: float = 8.0):
        """The Zen catalog is public and works no matter which credential mode is active."""
        try:
            response = httpx.get(CATALOG_URL, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            models = [m["id"] for m in data.get("data", []) if m.get("id")]
            return sorted(models) if models else None
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.debug("OpenCode Go catalog fetch failed: %s", exc)
        return None

    def fetch_account_usage(self, *, base_url: str | None = None, api_key: str | None = None):
        """Go plan windows via ``/zen/go/v1/usage`` (percent-based).

        That endpoint answers static Go API keys; console-session (device
        sign-in) credentials are not accepted there and have no external
        windows endpoint, so signed-in mode reports no usage."""
        from agent.account_usage import AccountUsageSnapshot, AccountUsageWindow
        from hermes_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested=self.name, explicit_base_url=base_url, explicit_api_key=api_key)
        token = str(runtime.get("api_key", "") or "").strip()
        if not token or token.startswith("st_"):
            return None
        with httpx.Client(timeout=10.0) as client:
            response = client.get("https://opencode.ai/zen/go/v1/usage",
                                  headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
            if response.status_code in (401, 403):
                return None
            response.raise_for_status()
        usage = (response.json() or {}).get("usage") or {}
        windows = []
        for key, label in (("rolling", "Rolling window"), ("weekly", "Weekly"), ("monthly", "Monthly")):
            window = usage.get(key) or {}
            if window.get("percent") is None:
                continue
            reset_raw = str(window.get("resetsAt") or "").replace("Z", "+00:00")
            reset_at = datetime.fromisoformat(reset_raw) if reset_raw else None
            windows.append(AccountUsageWindow(label=label, used_percent=float(window["percent"]), reset_at=reset_at))
        return AccountUsageSnapshot(provider=self.name, source="go_usage_api",
                                    fetched_at=datetime.now(UTC), windows=tuple(windows))


def _registered_hidden() -> bool:
    """Listed in pickers once a way in exists: a pooled sign-in or a static env key.

    Kept import-time-cheap (os only) to stay inside the discovery contract; live pool state
    still flips `listed()` through the default behavior once a row exists.
    """
    return not os.environ.get("OPENCODE_GO_API_KEY")


opencode_go = OpenCodeGoSigninProfile(
    name=PROVIDER_ID,
    aliases=("opencode_go", "go", "opencode-go-sub"),
    display_name="OpenCode Go (sign in)",
    description="OpenCode Go subscription — Sign in with OpenCode (device flow; OAuth auto-refresh)",
    signup_url="https://opencode.ai/docs/go/",
    env_vars=("OPENCODE_GO_API_KEY", "OPENCODE_GO_BASE_URL"),
    base_url="https://opencode.ai/zen/go/v1",
    auth_type="oauth_external",
    auth_handler=opencode_go_auth_handler,
    refresh_credential=opencode_go_refresh_credential,
    default_headers=dict(_ATTRIBUTION_HEADERS),
    default_aux_model="glm-5.3-flash",
    # The Go relay validates tool content as a strict string: list-type tool content
    # (native vision embeds) 422s/400s and poisons the conversation. Images in user
    # messages are fine, so vision keeps working via the text-summary downgrade.
    supports_vision_tool_messages=False,
    hidden=_registered_hidden(),
    fallback_models=(
        "glm-5.3-flash",
        "glm-5.3",
        "kimi-k2.6",
        "kimi-k3",
        "minimax-m3",
        "deepseek-v4-pro",
        "qwen3.8-max",
        "claude-haiku-5-5",
    ),
)

register_provider(opencode_go)
