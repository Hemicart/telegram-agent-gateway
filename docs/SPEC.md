# Telegram Agent Gateway — Implementation Spec

**Status:** LOCKED for implementation (2026-10-24) · **Rev 2** (2026-10-24): review fixes applied, see §22  
**Audience:** Coding agent (and any human deploying or reviewing)  
**Product in one sentence:** Open-source single-tenant Telegram→AI agent gateway — clone from GitHub, deploy anywhere *you* pay for, connect Telegram + an `Agent` folder, give your AI the URL and token.

Privacy model is simple and intentional: **you are the host**. There is no company fleet, no shared multi-tenant backend, and no “host is blind” claim. Your Telethon session stays on *your* volume.

---

## 1. How to use this document

| Reader | What to do |
|---|---|
| **Coding agent building the product** | Implement §6–§18 in order. Treat §11–§14 as P0. Do not build anything listed in §19. |
| **Coding agent deploying for a user** | Follow `docs/AGENT_DEPLOY.md` (canonical prompt in §15). Stop at `/setup` — human finishes Telegram login. |
| **Human / teammate** | Open the onboarding page (`landing/` or `/` on a fresh deploy) and follow the numbered steps. |

This file is the single source of truth. When code and this doc disagree, fix the code or update this doc in the same PR — do not invent a second design.

---

## 2. Product overview

### 2.1 What it does

A long-lived process that:

1. Logs into Telegram **as the user** (MTProto / Telethon), not as a BotFather bot.
2. Treats chats placed in a Telegram folder named **`Agent`** (configurable) as the allowlist.
3. Exposes an authenticated HTTP API so an AI agent can list those chats, paginate history, and download media.
4. **Never** gives the AI agent the Telegram session, `api_hash`, or access to chats outside the folder.

### 2.2 Architecture

```
Telegram (as YOU)
    ↕ MTProto session (Telethon) — lives on YOUR volume at /data
Gateway process (YOU host & pay)
    ├── Static onboarding page (landing/)
    ├── Setup wizard (/setup) — QR login primary
    └── Authenticated agent API (/v1/...)
            ↕ Bearer AGENT_API_TOKEN
Your AI agent (Grok / Claude / custom)
```

The agent only calls endpoints that already filter by folder membership. Raw Telegram access never leaves the gateway.

### 2.3 Non-goals of v1

- Sending, editing, or deleting Telegram messages
- Secret chats
- Multi-tenant / company-billed fleet hosting
- Zero-knowledge / “host cannot read” cryptography
- Using the Bot API as the data plane

---

## 3. End-user journey

What a teammate (or anyone) actually does:

**Deploy comes first; the deployed server is the guide.** The landing page only gets people to a running server. Everything after that happens on *their own* server’s wizard, so keys and login never touch anything else.

1. Landing page (GitHub Pages / README): **fork** the repo.
2. **Deploy** the fork to a platform they pay for (Railway / Render / Fly / Docker): one-click buttons or “tell your coding agent to deploy this repo.”
3. **Open the server’s URL.** While setup is incomplete, `/` redirects to `/setup`. The wizard then walks them through, one screen per step:
   1. **Start** — press Start; the first browser to do so claims the server (owner key in a long-lived cookie). Optional `SETUP_PASSWORD` env var makes this step ask for it.
   2. **Telegram keys** — numbered instructions for my.telegram.org (phone → in-app code → API development tools → form values → copy), then paste `api_id` / `api_hash`. *(Each person needs their own — real friction; the wizard makes it painless rather than hiding it.)*
   3. **Log in** — QR from Telegram → Settings → Devices → Link Desktop Device; 2FA prompt follows; phone + code as fallback.
   4. **Agent folder** — numbered instructions to create folder `Agent` and add chats one by one (no chat-type toggles); “Check folder” shows the chat titles.
   5. **Connect agent** — create the token (shown once) and get a ready-to-paste instruction block (base URL, auth header, endpoints, error meanings) for Claude / Grok / a custom agent, plus a **Test** button that calls `/v1/chats` with the token.
   6. **Done** — setup locks; emergency stop, rotate token, change setup.
4. After setup, `/` serves the docs page. Token + session stay on their instance only.

**Kill switch:** Telegram → Settings → Devices → terminate the gateway’s session. This revokes everything instantly, whatever state the host is in.

Cost: roughly **$3–8/month** order-of-magnitude for an always-on small instance **with a persistent disk**. Exact prices change; docs must say “always-on + disk required,” not a fixed dollar amount.

---

## 4. One-page onboarding website

### 4.1 Why

People need a single URL that teaches: get Telegram API credentials → deploy → log in → create folder → connect agent. The gateway is an HTTP server anyway, so it ships and serves this page.

### 4.2 Requirements

| Item | Requirement |
|---|---|
| Source | `landing/index.html` — single self-contained file (inline CSS, no build step) |
| Served by gateway | Always at `/docs`; at `/` once setup is complete. While setup is incomplete `/` redirects to `/setup` |
| Content | Three steps (fork → deploy → open your server) plus what the wizard will do; placeholder links `YOUR_ORG`, Deploy to Render / Railway |
| Design | Arcade “Essence” look: paper/ink, Work Sans labels, Geist sentences, Archivo condensed display (free stand-in for Montreal, which is licensed and must not ship) |
| Tone | Clear, portable, not an official Telegram product |
| Forms | **No** central signup that collects passwords for a hosted service. Instructional only; interactive login happens on the user’s own `/setup` |
| Mobile | Responsive, readable on phones |

Starter file lives at `landing/index.html` in this workspace. The coding agent replaces placeholders when the real GitHub repo exists.

---

## 5. Why not the Bot API

Official Bot API (`api.telegram.org` + BotFather token):

- Sees only chats the bot is in.
- Does **not** read personal DMs as you.
- Does **not** get history from before the bot joined.
- Privacy mode further limits groups unless disabled / bot is admin.
- Bot identity is visible to everyone.

For personal / business DMs + selective group context for a private agent, Bot API is the wrong surface.

Docs: https://core.telegram.org/api

---

## 6. MTProto setup (`api_id` / `api_hash`)

1. Register an application at https://my.telegram.org → **API development tools**.
2. Receive **`api_id`** (int) and **`api_hash`** (string).
3. Log in with the **user phone number** (QR preferred in our wizard; SMS/Telegram code + optional 2FA password as fallback).
4. Persist a **session** under `/data`. Reuse it; do not re-login every boot.

Reference: https://core.telegram.org/api/obtaining_api_id

### 6.1 Library choice

| Library | Language | Notes |
|---|---|---|
| **Telethon** (recommended) | Python | Mature; `iter_messages`, `download_media`, `GetDialogFiltersRequest`, QR login |
| Pyrogram | Python | Similar |
| TDLib | C++ / bindings | Official Telegram Database Library |
| teleproto / GramJS | TypeScript | If stack is TS-only |

**Default for this project: Telethon** unless the rest of the stack is strongly TypeScript-only.

### 6.2 Telegram abuse / ToS notes

- Unofficial clients are monitored for abuse.
- Flooding can get the **account** restricted or banned.
- Keep the gateway **read-heavy**, paced, ToS-compliant: https://core.telegram.org/api/terms
- Treat `api_hash` and the session file as **password-equivalent secrets**.
- Read-only keeps ban risk low, but it is not the only trigger. Also risky: **brand-new accounts**, repeated re-logins (e.g. lost volume), and history/search bursts that hit FloodWait. Use an established account; the first login from a datacenter IP may show a “new login” security notice — that is expected.
- `my.telegram.org` app creation sometimes fails with a bare “ERROR” (known Telegram issue: try another network/browser, no VPN). Docs should mention it.

---

## 7. Chat identity model (IDs)

Every conversation (“peer” / “dialog”) has a stable numeric identity.

### 7.1 Peer kinds

| Kind | Meaning | Typical Telethon `dialog.id` |
|---|---|---|
| User (DM) | 1:1 | Positive, e.g. `123456789` |
| Basic group (“chat”) | Legacy small groups | Negative, e.g. `-987654321` |
| Channel / megagroup | Channels + most modern groups | Bot-style negative, e.g. `-1001234567890` |

Raw MTProto IDs for users / chats / channels overlap. Normalize to **Bot API dialog IDs** so one integer uniquely identifies any peer.

Conversion (https://core.telegram.org/api/bots/ids):

- **User:** `botApiId = userId`
- **Basic chat:** `botApiId = -chatId`
- **Channel / megagroup:** `botApiId = -(10**12 + channelId)`

Telethon’s `dialog.id` / `utils.get_peer_id` already use this unified style. **Store Telethon `dialog.id` (or equivalent) and never mix raw channel ids with bot-api ids in one list.**

### 7.2 What to store

```json
{
  "dialog_id": -1001234567890,
  "title": "Arcade Growth",
  "kind": "supergroup",
  "username": null,
  "added_at": "2026-10-24T18:00:00Z"
}
```

- **`dialog_id`** = allowlist key (stable).
- **`title` / `username`** = labels only (can change).
- Do **not** key security on title or folder name string alone without resolving filter id.

### 7.3 Discovering IDs (admin / debug)

```python
async for d in client.iter_dialogs():
    print(d.id, d.title or d.name, "user" if d.is_user else "group" if d.is_group else "channel")
```

Day-to-day ops should use the folder (§8), not manual ID hunting.

---

## 8. Folder-based allowlist (core product)

### 8.1 UX for the human

1. In the official Telegram app, create folder **`Agent`** (title configurable via `AGENT_FOLDER_TITLE`).
2. Turn **OFF** any “include all contacts / groups / bots / …” style checkboxes.
3. Manually add every DM / group / channel the agent may see to the folder’s **include** list.
4. Remove from folder = revoke agent access (after gateway refresh).

No bots invited.

### 8.2 API surface

- Method: `messages.getDialogFilters`  
  Telethon: `functions.messages.GetDialogFiltersRequest()`  
  https://core.telegram.org/method/messages.getDialogFilters  
  https://tl.telethon.dev/methods/messages/get_dialog_filters.html

- Filter object: `DialogFilter`  
  https://tl.telethon.dev/constructors/dialog_filter.html

| Field | Role |
|---|---|
| `id` | Folder id (int) — **persist after first resolve** |
| `title` | Display title (`TextWithEntities` in newer layers — extract plain text) |
| `include_peers` | **Whitelist** — primary allowlist |
| `pinned_peers` | Pinned inside folder; treat as allowed |
| `exclude_peers` | Explicit exclusions |
| `contacts` / `non_contacts` / `groups` / `broadcasts` / `bots` | Optional **auto-include type flags** — dangerous |
| `exclude_muted` / `exclude_read` / `exclude_archived` | Extra filters — not a security boundary |

Also exists:

- `DialogFilterChatlist` — a folder the user **shared via invite link**. It has `include_peers` / `pinned_peers` but **no type flags**, so it is safe: treat it exactly like `DialogFilter` with all flags off.
- `DialogFilterDefault` (“All chats”) — never a match; skip.
- Any other / unknown type — skip.

If **more than one** folder matches the title → refuse (`AmbiguousFolder`) and tell the user to rename one. Never pick the first.

### 8.3 Hard-fail rule (security)

```
allowed_peers = set(include_peers) ∪ set(pinned_peers)
allowed_peers -= set(exclude_peers)

# HARD FAIL unless ALLOW_BROAD_FOLDER=1
if filter.contacts or filter.non_contacts or filter.groups
   or filter.broadcasts or filter.bots:
    refuse()  # would expose far more than intended
```

Resolve each `InputPeer` to a canonical `dialog_id` **offline** with `utils.get_peer_id(input_peer)` — the folder’s peers already carry `access_hash`, so no network call is needed. Do **not** call `get_entity` per peer on every refresh (flood risk at 100–200 peers × every 60s).

Keep a map `dialog_id → InputPeer` from the folder and pass that `InputPeer` (not the bare int) to `iter_messages` / `download_media`. A bare int id fails if the entity isn’t in the session’s entity cache.

### 8.4 Pin the folder by id **and** title

Folder ids are small integers (2–255) chosen by the Telegram app, and a **freed id is reused** by the next folder the user creates. Matching on id alone means: user deletes `Agent`, later creates “Family” → gateway silently exposes “Family”.

1. First successful resolve by `AGENT_FOLDER_TITLE` → persist `{folder_id, title}` under `/data`.
2. Subsequent refreshes: find the filter by `folder_id`, then require `plain_title == persisted title`.
3. **Title mismatch → fail closed:** empty the allowlist, agent API returns `503 folder_needs_confirmation`, `/health` shows `folder_ok: false`. The user confirms the new title in `/setup` (password-gated), which re-persists `{folder_id, title}`.
4. `folder_id` missing from the filter list → fail closed the same way (folder deleted). Do not auto-fall back to title search without confirmation.

A legitimate rename costs one click in `/setup`; a reused id can never leak a different folder.

### 8.5 Refresh policy

- Cache `dialog_id → InputPeer` in memory.
- Refresh on:
  - `UpdateDialogFilter` / `UpdateDialogFilters` / `UpdateDialogFilterOrder` events (Telethon `events.Raw`) — near-instant revoke;
  - TTL 60s as a backstop;
  - `POST /admin/refresh-allowlist`; setup “Refresh” click.
- Every agent request: `if dialog_id not in allowlist: 404` **before** any Telegram history/media call.

**Fail-closed rules** (a refresh **result**, not just the boot check):

| Refresh outcome | Action |
|---|---|
| Broad type flag now on | Empty allowlist immediately; `503 folder_insecure` |
| Folder missing / title mismatch / ambiguous | Empty allowlist; `503 folder_needs_confirmation` |
| Refresh call fails (network, FloodWait) | Keep last good allowlist for up to `ALLOWLIST_STALE_MAX_SECONDS` (default 300), then empty it; `503 allowlist_stale` |

Never serve from an allowlist older than the stale limit.

### 8.6 Do NOT use `iter_dialogs(folder=N)` for custom folders

Telethon `iter_dialogs(folder=N)` uses archive-style folder ids (`0` main, `1` archive), **not** custom `DialogFilter` ids.  
**Always** use `GetDialogFiltersRequest` + peer resolution for the Agent folder.

### 8.7 Allowlist algorithm (pseudo)

```
FOLDER_TYPES = (types.DialogFilter, types.DialogFilterChatlist)
BROAD_FLAGS = ("contacts", "non_contacts", "groups", "broadcasts", "bots")

async def load_allowlist(client, pinned: PinnedFolder | None, folder_title: str) -> dict[int, InputPeer]:
    result = await client(functions.messages.GetDialogFiltersRequest())
    folders = [f for f in result.filters if isinstance(f, FOLDER_TYPES)]

    if pinned is not None:
        target = next((f for f in folders if f.id == pinned.folder_id), None)
        if target is None or plain_title(target.title) != pinned.title:
            raise FolderNeedsConfirmation(...)   # deleted, renamed, or id reused
    else:
        matches = [f for f in folders if plain_title(f.title) == folder_title]
        if not matches:
            raise FolderNotFound(...)
        if len(matches) > 1:
            raise AmbiguousFolder(...)
        target = matches[0]
        persist_pinned_folder(target.id, plain_title(target.title))

    if any(getattr(target, flag, False) for flag in BROAD_FLAGS) and not ALLOW_BROAD_FOLDER:
        raise InsecureFolderConfig("Disable type-based includes on Agent folder")

    exclude = {utils.get_peer_id(p) for p in getattr(target, "exclude_peers", [])}
    allowed = {}
    for p in list(target.include_peers) + list(target.pinned_peers):
        did = utils.get_peer_id(p)          # offline, no network call
        if did not in exclude:
            allowed[did] = p                # keep InputPeer (has access_hash)
    return allowed
```

`plain_title()` must accept both `str` (older layers) and `TextWithEntities` (newer). Pin a Telethon version that ships the newer layer.

---

## 9. History pagination & media

### 9.1 History reality

History is fetched in pages. Flood limits apply (Telethon notes ~30s per 10 history requests at scale; `wait_time` kicks in for large pulls).

Telethon:

- `client.iter_messages(entity, limit=..., offset_id=..., min_id=..., max_id=..., reverse=..., search=..., filter=...)`
- `client.get_messages(...)`

https://docs.telethon.dev/en/stable/modules/client.html

### 9.2 Agent pagination contract

Do **not** dump multi-year histories into one agent context.

**`GET /v1/chats/{dialog_id}/messages`**

| Param | Meaning |
|---|---|
| `limit` | Page size (default 50, max 100) |
| `before_id` | Messages with id **strictly older** than this |
| `after_id` | Messages **newer** than this |
| `around_id` | Optional window around a message |
| `topic_id` | Optional: only messages in this forum topic (supergroups with Topics) |
| `search` | Optional server-side text search |
| `media_only` | Optional photos/docs filter |

`before_id`, `after_id`, `around_id` are mutually exclusive → `400` if more than one is set.

Response:

```json
{
  "dialog_id": -1001234567890,
  "messages": [
    {
      "id": 4217,
      "date": "2026-10-20T14:02:11Z",
      "sender_id": 123456789,
      "sender_name": "Alice",
      "text": "Hello",
      "reply_to_msg_id": null,
      "media": {
        "type": "photo",
        "mime": "image/jpeg",
        "download_path": "/v1/media/-1001234567890/4217"
      }
    }
  ],
  "next_before_id": 4100,
  "has_more": true
}
```

Mapping (`peer` = the `InputPeer` from the allowlist map, §8.3):

| Request | Telethon call | Returned order |
|---|---|---|
| default / `before_id` | `iter_messages(peer, limit=L+1, offset_id=before_id or 0)` | newest → oldest |
| `after_id` | `iter_messages(peer, limit=L+1, min_id=after_id, reverse=True)` | oldest → newest, starting right after `after_id` |
| `around_id` | `iter_messages(peer, limit=L, offset_id=around_id, add_offset=-(L // 2))` | newest → oldest |
| `topic_id` | add `reply_to=topic_id` to any of the above | — |

⚠️ Do **not** map `after_id` to `min_id` without `reverse=True`: that returns the *newest* L messages above `after_id` and silently skips the gap.

- Fetch `L+1`, return `L`; `has_more` = an extra row came back (no off-by-one empty page).
- Response always says its order: `"order": "desc" | "asc"`.
- `next_before_id` = smallest id in a desc page; `next_after_id` = largest id in an asc page.

### 9.3 Optional full-history jobs

If an indexer needs all history:

1. Background job per `dialog_id`, gated by allowlist.
2. Walk with `iter_messages(..., wait_time=1)` (or takeout later).
3. Store normalized rows in Postgres keyed by `(dialog_id, message_id)`.
4. Agent reads deep search from **your DB**; live path for “latest N”.

Takeout (later): `client.takeout()`; handle `TakeoutInitDelayError`.

### 9.4 Search

- In-chat: `iter_messages(peer, search="query")`
- **v1: `dialog_id` is required on `/v1/search`.** Cross-chat search would run one `messages.search` per allowlisted chat per query — a FloodWait generator. Never use `messages.searchGlobal` (it searches the whole account).
- Cross-chat search comes later from the local DB (§9.3), not live Telegram calls.

### 9.5 Media

Messages may include photos, documents, stickers, voice, video, video notes.

- Download: `await client.download_media(message, file=...)`
- Thumbs: `download_media(..., thumb=0)`

Agent-facing design:

1. Message JSON includes `media` metadata only — no raw Telegram file refs as public URLs.
2. **`GET /v1/media/{dialog_id}/{message_id}`** (auth required): verify allowlist → download or cache → stream with correct `Content-Type`. The allowlist check runs on **cache hits too**.
3. `?thumb=1` for small previews (preferred for LLM vision).
4. Cache under `/data/media-cache` keyed by `(dialog_id, message_id, thumb|full)` with TTL. Total cache capped by `MEDIA_CACHE_MAX_BYTES` (LRU evict). When a chat leaves the allowlist, delete its cache directory.
5. Size caps: check the size in message metadata **before** downloading; refuse full download > `MEDIA_MAX_BYTES` (20MB); thumbs always allowed when available.

**Response headers (P0, XSS):** files come from other chat members and are served from the same origin as `/setup`. A crafted `.html`/`.svg` would otherwise run script against the wizard.

```
X-Content-Type-Options: nosniff
Content-Security-Policy: sandbox; default-src 'none'
Content-Disposition: attachment; filename="<sanitized>"   # everything except raster images
Content-Disposition: inline                               # only image/jpeg, image/png, image/webp, image/gif
```

`image/svg+xml` is **never** inline. `/setup` pages also set `frame-ancestors 'none'`.

**Secret chats:** out of scope for v1 (E2E; third-party clients generally cannot usefully expose them).

---

## 10. Full HTTP API contract

Base: `https://<your-deploy>/v1`  
Agent auth: `Authorization: Bearer <AGENT_API_TOKEN>`

### 10.1 Agent endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/v1/chats` | List allowlisted chats |
| GET | `/v1/chats/{dialog_id}` | One chat metadata (404 if not allowed) |
| GET | `/v1/chats/{dialog_id}/messages` | Paginated history (§9.2) |
| GET | `/v1/media/{dialog_id}/{message_id}` | Image/file bytes |
| GET | `/v1/search` | `q` + **required** `dialog_id` (§9.4); allowlisted only |

### 10.2 Admin / setup endpoints

**Admin auth = the owner key** (or `SETUP_PASSWORD` if set), sent as `Authorization: Basic` or via the owner cookie. There is no second admin credential. Locking setup closes the *wizard* (re-login, credential change), not the admin endpoints below.

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Liveness/readiness JSON (§10.4) — **no auth** |
| GET | `/` or `/docs` | Onboarding static page |
| GET/POST | `/setup/*` | Wizard (§12) — requires `SETUP_PASSWORD`; locked after complete |
| POST | `/setup/unlock` | Re-open the wizard (owner) |
| POST | `/setup/api/claim` | First visitor becomes owner; `409` once claimed |
| POST | `/setup/api/login` | Sign in from another browser with the owner key |
| POST | `/admin/refresh-allowlist` | Force folder re-read |
| POST | `/admin/confirm-folder` | Accept a renamed folder (§8.4) |
| POST | `/admin/rotate-token` | New `AGENT_API_TOKEN`, old one invalid immediately; new value shown once |
| GET | `/admin/folder-debug` | Resolved folder title, id, flags, peer ids |

Login attempts on `/setup` and `/admin/*`: max 5 failures per IP per 15 min, then `429`; after 20 total failures, lock admin for 1 hour. Compare secrets in constant time.

### 10.3 Hard rules

1. Every agent handler resolves allowlist **first**.
2. Unknown / non-allowed `dialog_id` → **`404`** (do not leak existence). Stay consistent.
3. No endpoint returns “all dialogs on the account”.
4. No send/edit/delete in v1.
5. Rate-limit per token; coalesce Telegram calls.
6. Read-only v1 is mandatory for ship.

### 10.4 `/health` contract

```json
{
  "ok": true,
  "version": "0.1.0",
  "telegram": "connected" | "disconnected" | "not_configured",
  "setup_complete": true,
  "folder_ok": true
}
```

- HTTP 200 when process is up (even if Telegram disconnected — use fields for readiness).
- Orchestrators may treat `ok && setup_complete && telegram=="connected"` as ready for agent traffic.
- Never include secrets, tokens, phone numbers, or chat titles here.

---

## 11. Security requirements (P0)

These are product requirements, not suggestions.

| # | Requirement |
|---|---|
| 1 | **Volume persistence mandatory.** Telethon session lives under `/data`. Every deploy target mounts a persistent volume/disk there. Ephemeral filesystem = endless re-login = broken product. |
| 2 | **Setup gate = first-visitor claim** (§12.3); optional `SETUP_PASSWORD`. Claim window = deploy → owner opens URL. Acceptable on PaaS (wildcard certs, so new hostnames don’t appear in CT logs); on a VPS with its own domain, open it immediately or set `SETUP_PASSWORD`. Wrong owner keys hit the §10.2 lockout. |
| 3 | **Disable / lock `/setup` after complete.** Further changes need `SETUP_PASSWORD` + explicit “unlock setup” (or SSH/volume wipe). A public open `/setup` is a hijack window. |
| 4 | **Strong `AGENT_API_TOKEN`** (cryptographically random, ≥32 bytes). Source and storage per §12.3. Rotatable via `/admin/rotate-token`. Never equal to Telegram credentials. |
| 5 | Session file + `api_hash` only on the gateway host / volume. Never in agent prompts, git, or landing page. |
| 6 | Allowlist checked on **every** messages/media call (including media cache hits). Broad folder type flags → hard fail unless `ALLOW_BROAD_FOLDER=1`. Fail-closed rules per §8.5. |
| 7 | Pin the folder by `folder_id` **and** title; mismatch fails closed until confirmed (§8.4 — ids are reused). |
| 8 | TLS: PaaS (Railway/Render/Fly) provide HTTPS. **Home `docker compose` does not** — document reverse proxy (Caddy/Traefik/nginx) or tunnel; do not pretend compose alone is internet-safe. |
| 9 | FloodWait → sleep / surface HTTP 429. No auto-mark-as-read in v1. |
| 10 | Audit log: which token read which `dialog_id` / message range (no message bodies in logs). |
| 11 | **Exactly one process per session, ever.** Two clients on the same auth key get `AUTH_KEY_DUPLICATED` and Telegram **revokes the session permanently** (user must log in again). Causes: replicas, `fly launch` default HA (2 machines), zero-downtime / blue-green / overlapping deploys. Enforce on boot: take an exclusive `flock` on `/data/gateway.lock`; if held, log and exit non-zero. Every deploy target in §14 must stop the old instance before starting the new one. |
| 12 | Media responses carry the anti-XSS headers in §9.5. |
| 13 | Ship `LICENSE`, `SECURITY.md`, pin image digests in docs. `SECURITY.md` covers: how to report vulns; what the host can see (*you*, the deployer); the kill switch (§3); **supply chain** — whoever controls the repo/image you run can read the session, so pin a digest and review before updating; chat content reaching the agent is untrusted input (anyone in an allowlisted chat can write text aimed at your agent). |
| 14 | Container runs as a non-root user; `/data` is `0700`, session file `0600`. |
| 15 | **HTTPS only for secrets.** `/setup`, `/setup/api/*`, `/admin/*` and `/v1/*` return `403 https_required` (setup page: a “use your https address” screen) unless the request is HTTPS — directly, or via a TLS proxy’s `X-Forwarded-Proto: https` — or comes to localhost. `/health` and `/docs` stay open. HSTS on HTTPS responses; setup cookie `Secure` behind HTTPS. Escape hatch `ALLOW_PLAIN_HTTP=1` logs a warning on boot; docs and agent prompts say never to set it. |

---

## 12. Setup wizard UX (QR primary)

Route: `/setup` (gated by `SETUP_PASSWORD`).

### 12.1 Flow

1. **Start:** first visitor claims the server (§12.3); later visitors need the owner key.
2. **Credentials:** `api_id`, `api_hash` with link to my.telegram.org.
3. **Login (QR primary):**
   - Show Telethon `qr_login` QR code; wait until confirmed in the official Telegram app.
   - The QR token expires in ~30s: loop `await qr.wait(timeout=25)` → on `TimeoutError` call `await qr.recreate()` and redraw. Give up after ~5 min with a “Try again” button.
   - **2FA on the QR path:** if the account has a cloud password, `qr.wait()` raises `SessionPasswordNeededError`. Show a password field and call `client.sign_in(password=...)`. Without this step, QR login fails for every 2FA account.
   - Fallback: phone number → login code → 2FA password if enabled.
4. **Folder:** detect `Agent` (or configured title). If missing, show instructions + Refresh. Persist `{folder_id, title}` on success. Hard-fail UI if broad flags on or two folders share the title.
5. **Allowlist preview:** chat titles (ids hidden by default; toggle to show).
6. **Agent token:** reveal / rotate `AGENT_API_TOKEN` once; copy-paste snippet for connecting an AI agent (`BASE_URL` + token).
7. **Complete:** mark setup complete under `/data`; lock `/setup`.

### 12.2 Why QR first

Phone + SMS code on a remote PaaS browser session is awkward (code arrives on the phone while you’re in a cloud UI). QR login lets the user confirm in the official app on the same phone. Keep phone/2FA as fallback for accounts/environments where QR fails.

### 12.3 First-boot secrets

**No secret to dig out of logs.** Non-technical users never found it, so the setup gate is trust-on-first-use:

| Secret | If env var is set | If not set | Stored in `/data` |
|---|---|---|---|
| Claim | `SETUP_PASSWORD` must be entered to claim | The first browser to press **Start** claims the server (atomic `link()` of `owner.json`; later claims `409`). Refused outright once keys are saved, Telegram is logged in, or setup is complete, so upgraded servers are never up for grabs | `owner.json`: claim marker |
| Owner sessions | — | Every claim/sign-in issues a random session ID as an `HttpOnly; Secure; SameSite=Strict` cookie (~400 days). Only its SHA-256 is stored; checks are a cheap lookup (no secret in the cookie, no per-request KDF). “Sign out other browsers” drops all other hashes | `sessions.json` (max 20) |
| Other browsers | — | Sign in with the Telegram **api_id + api_hash** entered in setup (recoverable at my.telegram.org), or `SETUP_PASSWORD`. api_hash attempts count per visitor IP only (first `X-Forwarded-For` hop), so strangers can't lock the owner out; password attempts also count toward the global lockout. Telegram's public demo api_ids are rejected at the keys step | — |
| VPS (DigitalOcean) | — | Droplet addresses get their own certificate and show up in public CT logs, so `setup.sh` generates a `SETUP_PASSWORD` and shows it on SSH login / the web console | `.env` (0600) |

Trade-off accepted: a leaked `api_hash` now also grants gateway admin (new agent token → folder chats). Docs and the Done screen say to keep it private.
| `AGENT_API_TOKEN` | Use it; `/admin/rotate-token` is disabled (rotate in the platform secret store) | Generate (≥32 bytes); **not** logged; shown once in setup step 6 | SHA-256 hash only |

- Do not regenerate on restart if the hash files already exist.
- Because only hashes are stored, “shown once” is literal: a lost token means `/admin/rotate-token`.
- Setting an env var later overrides the stored value on next boot (log a one-line notice, no values).

---

## 13. Repo layout & required assets

```
telegram-agent-gateway/
  README.md                 # 60-second overview + deploy buttons
  LICENSE
  SECURITY.md
  Dockerfile                # HEALTHCHECK → GET /health
  docker-compose.yml        # volume tgdata:/data
  render.yaml               # disk on paid plan; autoDeployTrigger commit (deploys from the user's fork)
  fly.toml                  # [mounts] for /data
  railway.toml | docs notes # volume attached; single replica
  .env.example
  docs/
    SELF-HOST.md            # step-by-step per target
    AGENT_DEPLOY.md         # paste-ready coding-agent prompt (§15)
  landing/
    index.html              # one-page onboarding
  src/                      # gateway application
```

CI: build → push `ghcr.io/YOUR_ORG/telegram-agent-gateway:<version>` **and** digest; docs recommend pinning digest.

| Asset | Why |
|---|---|
| `README.md` + deploy buttons | Humans |
| `docs/SELF-HOST.md` | Humans + agents |
| `docs/AGENT_DEPLOY.md` | “Paste into Cursor/Grok” |
| `Dockerfile` + healthcheck | Universal |
| `docker-compose.yml` | Local / NAS |
| `render.yaml` | One-path Render |
| `fly.toml` | One-path Fly |
| Railway template or docs | One-path Railway |
| Setup wizard | Avoid CLI login pain after deploy |
| `landing/index.html` | Portable instructions for anyone |
| `.env.example` | No guesswork |
| `LICENSE`, `SECURITY.md` | Trust + disclosure |

---

## 14. Deploy targets (volume-first)

**Rule:** if the platform cannot mount persistent storage at `/data`, it is not a supported happy path.

### 14.1 Dockerfile (sketch)

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN useradd --uid 10001 --create-home app && mkdir -p /data && chown app:app /data
USER app
ENV DATA_DIR=/data
EXPOSE 8080
# python:*-slim has no curl — use the stdlib
HEALTHCHECK CMD python -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3)" || exit 1
CMD ["python", "-m", "gateway"]
```

Some PaaS volumes mount as root-owned; if so, `chown` `/data` in an entrypoint before dropping to `app`.

### 14.2 docker compose

```yaml
services:
  gateway:
    # pinned digest by default — update deliberately (see SECURITY.md supply chain)
    image: ghcr.io/YOUR_ORG/telegram-agent-gateway@sha256:REPLACE_WITH_RELEASE_DIGEST
    ports:
      - "8080:8080"
    volumes:
      - tgdata:/data
    env_file: .env
    restart: unless-stopped
volumes:
  tgdata:
```

Do not run `docker compose up --scale gateway=2` or start a second copy against the same volume (§11 #11; the boot lock will refuse it).

**TLS note:** compose publishes plain HTTP, and the gateway refuses setup over plain http from anything but localhost (§11 #15). For internet exposure, put Caddy/Traefik/nginx or a tunnel in front with a real domain; the proxy must send `X-Forwarded-Proto: https`. PaaS paths below already terminate HTTPS.

### 14.3 Render

- `render.yaml` Blueprint + **Deploy to Render** button.
- **Persistent disk requires a paid web service plan.** Free/ephemeral filesystem will silently lose the session — document this explicitly.
- `autoDeployTrigger: commit`: users deploy from **their own fork**, which only changes when they push or press “Sync fork”, so auto-deploy adds no supply-chain exposure and removes the “why didn’t it update?” confusion (seen in testing). Upstream pushes never reach a fork on their own. Note: the Deploy button builds from the **upstream repo’s HEAD**; users who want a pinned build should fork first (SECURITY.md supply chain).
- A service with a disk gets no zero-downtime deploy (old instance stops first) — this is what we want. Keep instance count at 1; never enable scaling.

```yaml
# render.yaml sketch
services:
  - type: web
    name: telegram-agent-gateway
    runtime: docker
    plan: starter   # paid — required for disks
    healthCheckPath: /health
    disk:
      name: tgdata
      mountPath: /data
      sizeGB: 1
    envVars:
      - key: DATA_DIR
        value: /data
```

Button:

```markdown
[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/YOUR_ORG/telegram-agent-gateway)
```

### 14.4 Railway

- Template or GitHub deploy into **their** Railway project.
- Attach a **volume** mounted at `/data`.
- Caveats to document: **one volume per service**, **no replicas** with a volume, **brief downtime** on redeploy when a volume is attached (the old deployment stops first — required, see §11 #11). Fine for single-tenant; say so.
- Generate `AGENT_API_TOKEN` / `SETUP_PASSWORD` as secrets if not using first-boot generation only (precedence per §12.3).

### 14.5 Fly.io

```toml
# fly.toml sketch
app = "YOUR_APP_NAME"
primary_region = "fra"

[build]
  dockerfile = "Dockerfile"

[http_service]
  internal_port = 8080
  force_https = true
  auto_stop_machines = false   # always-on for Telegram session
  auto_start_machines = true
  min_machines_running = 1

[mounts]
  source = "tgdata"
  destination = "/data"

[[vm]]
  size = "shared-cpu-1x"
  memory = "512mb"
```

Create volume once: `fly volumes create tgdata --size 1`.  
`fly launch --from https://github.com/YOUR_ORG/telegram-agent-gateway --ha=false` for power users.

⚠️ **`--ha=false` is mandatory.** By default Fly creates 2 machines for HA → two Telethon clients → `AUTH_KEY_DUPLICATED` → session revoked. Also never `fly scale count 2`. Keep the default rolling deploy strategy (with one machine + volume it stops before it starts); do not use `bluegreen`.

### 14.6 Cost framing

Always-on small instance **+ disk**: roughly **$3–8/month** depending on PaaS (order-of-magnitude; link current pricing in README). User owns billing and can destroy anytime (session dies with the volume unless they backed it up).

---

## 15. Canonical coding-agent deploy prompt

Ship this block verbatim in `docs/AGENT_DEPLOY.md` for copy-paste:

```text
Deploy the Telegram Agent Gateway from this repository as a single-tenant
service I own and pay for.

Requirements:
1. Use the repo Dockerfile (or compose / render.yaml / fly.toml) and docs/SELF-HOST.md.
2. Mount persistent storage at /data for the Telethon session (mandatory).
   - Railway: attach a volume; single replica only; expect brief downtime on redeploy.
   - Render: paid web plan + disk (free/ephemeral will lose the session).
   - Fly: [mounts] in fly.toml; create volume before first deploy; keep min_machines_running=1;
     launch with --ha=false (exactly one machine).
   - Compose: named volume tgdata:/data; put TLS reverse proxy in front if exposed to the internet.
3. Use an always-on / non-ephemeral plan suitable for a long-lived MTProto client.
   Run EXACTLY ONE instance: no replicas, no scaling, no zero-downtime / blue-green
   deploys. Two instances on one session make Telegram revoke it.
4. HTTPS ONLY. Expose a public https:// URL (PaaS TLS, or Caddy/another TLS reverse
   proxy with a real domain for VPS/compose). Never hand me a bare IP or http:// address:
   the server refuses setup, admin and agent API calls over plain http. Do not set
   ALLOW_PLAIN_HTTP.
5. Set env from .env.example. Generate strong SETUP_PASSWORD and AGENT_API_TOKEN
   if the image does not auto-generate them on first boot; store as platform secrets.
6. Never commit secrets, session files, api_hash, or tokens to git.
7. After deploy, verify GET https://<deploy-url>/health returns JSON.
8. Give me https://<deploy-url> right away and STOP: the first browser to press
   Start becomes the owner. I finish setup and the Telegram login (QR) myself.
   Do not open the setup page or attempt the Telegram login for me.
```

---

## 16. Environment variables

Document every variable in `.env.example`:

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TELEGRAM_API_ID` | After setup | — | From my.telegram.org (may be stored via wizard under `/data` instead) |
| `TELEGRAM_API_HASH` | After setup | — | Secret; password-equivalent |
| `DATA_DIR` | Yes | `/data` | Session, setup state, media cache, folder_id |
| `TELEGRAM_SESSION_PATH` | No | `$DATA_DIR/session` | Session file path |
| `AGENT_FOLDER_TITLE` | No | `Agent` | Folder title to find on first resolve |
| `AGENT_API_TOKEN` | No | auto-generated | Bearer token for `/v1`; env wins over stored (§12.3) |
| `SETUP_PASSWORD` | No | — | Optional: required to claim the server; also works as owner credential (§12.3) |
| `ALLOW_BROAD_FOLDER` | No | `0` | Set `1` only if you accept type-flag includes |
| `ALLOWLIST_STALE_MAX_SECONDS` | No | `300` | Max age of last good allowlist when refresh fails (§8.5) |
| `MAX_PAGE_SIZE` | No | `100` | Cap for messages `limit` |
| `MEDIA_CACHE_DIR` | No | `$DATA_DIR/media-cache` | Media cache root |
| `MEDIA_CACHE_MAX_BYTES` | No | `524288000` | 500MB total cache cap (LRU) |
| `MEDIA_MAX_BYTES` | No | `20971520` | 20MB full-download cap |
| `ALLOW_PLAIN_HTTP` | No | `0` | `1` lets setup/admin/agent API run over plain http. Dangerous; for private networks only |
| `PORT` | No | `8080` | HTTP listen port |
| `LOG_LEVEL` | No | `INFO` | Logging |

Never commit a filled `.env`. Prefer platform secret stores.

---

## 17. Implementation phases

### Phase 0 — Spike
- Telethon login with `api_id`/`api_hash` (QR incl. 2FA + recreate loop; phone fallback)
- Print `GetDialogFiltersRequest` for folder `Agent` (also test a shared `DialogFilterChatlist` folder)
- Resolve `include_peers` → dialog ids offline; confirm `folder=` dialog listing is **not** used

### Phase 1 — Core read API (ship blocker)
- `/data` session persistence + boot lock (`/data/gateway.lock`)
- Secrets per §12.3 (env wins, else generate once, hashes only)
- Allowlist cache + hard-fail broad flags + pin `{folder_id, title}` + fail-closed rules + filter update events
- `GET /health`, `GET /v1/chats`, `GET /v1/chats/{id}/messages` with `before_id` / `after_id` / `around_id`
- Bearer auth; admin endpoints + rotate-token; login rate limits
- Setup wizard (QR primary) + lock after complete
- Serve `landing/index.html`
- **Required tests:**
  - non-allowed id never calls Telegram history
  - folder id reused under a different title → allowlist empty, `503`
  - broad flag turned on mid-run → allowlist empty on next refresh
  - refresh failing past the stale limit → `503`
  - `after_id` returns the messages immediately after it, in ascending order
  - second process on the same `/data` exits

### Phase 2 — Media
- `GET /v1/media/...` + disk cache + thumbs + size limits + anti-XSS headers (§9.5) + purge on revoke
- Test: an `.html` / `.svg` document is served as `attachment` with `nosniff` + `CSP sandbox`

### Phase 3 — Distribution
- Dockerfile HEALTHCHECK, compose, `render.yaml` (paid disk), `fly.toml` mounts, Railway volume docs
- `README.md` deploy buttons, `docs/SELF-HOST.md`, `docs/AGENT_DEPLOY.md`
- `LICENSE`, `SECURITY.md`, GHCR image + digest pin docs

### Phase 4 — Agent integration & hardening
- Example snippets for connecting common AI agents
- FloodWait metrics
- Optional: Postgres sync / RAG + cross-chat search from the DB; optional `events.NewMessage` filtered by allowlist
- Optional takeout for bulk export

---

## 18. DTOs & Telethon reference sketches

### 18.1 Message DTO

```json
{
  "id": 1,
  "dialog_id": -1001234567890,
  "date": "ISO-8601",
  "out": false,
  "sender_id": 1,
  "sender_name": "string|null",
  "text": "string|null",
  "entities": [],
  "reply_to_msg_id": null,
  "topic_id": null,
  "fwd_from": null,
  "media": null
}
```

`topic_id` = forum topic the message belongs to (`reply_to.reply_to_top_id`, or `reply_to_msg_id` when `forum_topic` is set); `null` outside forum supergroups. `/v1/chats/{id}` exposes `"is_forum": true|false`.

Media when present:

```json
{
  "type": "photo|document|video|audio|voice|sticker|other",
  "mime": "image/jpeg",
  "size": 12345,
  "file_name": null,
  "width": 1280,
  "height": 720,
  "download_path": "/v1/media/{dialog_id}/{id}"
}
```

### 18.2 Telethon sketch (reference only)

```python
import asyncio
from telethon import TelegramClient, errors, events, functions, types, utils

client = TelegramClient(session_path, api_id, api_hash)

# Folder resolution + allowlist: see load_allowlist() in §8.7.

async def fetch_page(dialog_id: int, limit=50, before_id=0):
    allowlist = await get_allowlist()          # dict[dialog_id, InputPeer]; raises if stale/insecure
    peer = allowlist.get(dialog_id)
    if peer is None:
        raise NotFound()                       # 404 — never touch Telegram
    msgs = [m async for m in client.iter_messages(peer, limit=limit + 1, offset_id=before_id or 0)]
    return msgs[:limit], len(msgs) > limit     # (page, has_more)

async def qr_login_flow(show_qr, ask_password):
    qr = await client.qr_login()
    while True:
        show_qr(qr.url)
        try:
            await qr.wait(timeout=25)
            return
        except asyncio.TimeoutError:
            await qr.recreate()                # token expires ~30s
        except errors.SessionPasswordNeededError:
            await client.sign_in(password=await ask_password())   # 2FA accounts
            return

async def on_filters_changed(event):           # client.add_event_handler(..., events.Raw(
    await refresh_allowlist()                  #   (types.UpdateDialogFilter, types.UpdateDialogFilters,
                                               #    types.UpdateDialogFilterOrder)))
```

### 18.3 Backend module layout

```
/gateway
  /auth          # load session from /data, keep client connected
  /allowlist     # GetDialogFilters → resolve peers → cache; persist folder_id
  /chats         # list/get metadata for allowed only
  /messages      # paginated history
  /media         # download + cache
  /admin         # refresh, debug
  /setup         # password gate, QR wizard, lock
  /static        # landing/
  /telegram
     client.py   # Telethon singleton
     normalize.py
```

**Process model:** one long-lived process holding one Telethon client, with the HTTP server on the **same asyncio loop** (e.g. aiohttp or uvicorn with a single worker — never `--workers N`). Two processes on one session → `AUTH_KEY_DUPLICATED` → session revoked (§11 #11).

---

## 19. Out of scope / rejected ideas

Do **not** implement these as part of v1:

| Idea | Why rejected |
|---|---|
| Company-billed multi-tenant fleet on a shared host | Host process sees plaintext MTProto traffic; teammates rejected “not private” |
| “Host is blind” / zero-knowledge while session runs on the host | Cryptographically dishonest if the host decrypts sessions to serve agents |
| Local Bridge / enclave modes as v1 | Valid research paths later; not needed when each user is their own host |
| Bot API as the data plane | Cannot read personal DMs/history as the user |
| Secret chats | E2E; out of scope |
| Send/edit/delete message APIs | Read-only v1 lowers ban risk and matches “agent reads context” |

Short history: multi-tenant Modes H/L/E and company hosting were explored and **superseded** by personal self-host (this document). A coding agent must not resurrect them from old notes.

---

## 20. Sources

- Telegram APIs overview: https://core.telegram.org/api
- Obtaining `api_id` / `api_hash`: https://core.telegram.org/api/obtaining_api_id
- API ToS: https://core.telegram.org/api/terms
- Bot API dialog ID normalization: https://core.telegram.org/api/bots/ids
- `messages.getDialogFilters`: https://core.telegram.org/method/messages.getDialogFilters
- Dialog folders: https://core.telegram.org/api/folders
- Telethon client: https://docs.telethon.dev/en/stable/modules/client.html
- Telethon `GetDialogFiltersRequest`: https://tl.telethon.dev/methods/messages/get_dialog_filters.html
- Telethon `DialogFilter`: https://tl.telethon.dev/constructors/dialog_filter.html
- Railway templates: https://docs.railway.com/templates/deploy
- Render Deploy button: https://render.com/docs/deploy-to-render

---

## 21. One-sentence summary for the code agent

**Build a singleton Telethon user-client gateway that persists its session on `/data` (one process, ever), gates setup with a password and QR login (2FA-aware), treats a dedicated Telegram folder’s `include_peers` as the allowlist (hard-failing broad type flags, pinning `folder_id` + title, failing closed on any doubt), exposes authenticated paginated history + media HTTP APIs only for those dialog IDs, ships one-click volume-first deploy configs and a static onboarding page, and never gives the AI agent the Telegram session or any chat outside that folder.**

---

## 22. Changelog

### Rev 2 — 2026-10-24 (review fixes)

| Area | Change |
|---|---|
| Folder pinning (§8.4) | Match by id **and** title; mismatch fails closed until confirmed — folder ids are reused by Telegram apps |
| Allowlist (§8.2–§8.7) | Fail-closed table; filter-update events; offline peer ids + `InputPeer` map; `DialogFilterChatlist` supported; duplicate titles refused |
| Media (§9.5) | Anti-XSS headers (`nosniff`, `CSP sandbox`, `attachment`); cache purge on revoke; total cache cap; size checked before download |
| QR login (§12.1) | 2FA step on the QR path; ~30s token recreate loop |
| Single process (§11 #11, §14, §15) | `AUTH_KEY_DUPLICATED` explained; boot `flock`; Fly `--ha=false`; no overlapping deploys |
| Dockerfile (§14.1) | Healthcheck via Python stdlib (slim has no curl); non-root user |
| Pagination (§9.2) | Correct `after_id` / `around_id` mappings; `limit+1` for `has_more`; `topic_id` |
| Secrets (§12.3, §16) | Single precedence rule (env wins, else generate once); hashes only |
| Admin (§10.2) | Admin auth = owner key; `rotate-token`, `confirm-folder`, `unlock`; login rate limits |
| Setup gate (§12.3, Rev 3) | Log-printed setup password replaced by first-visitor claim; owner sign-in via api_id + api_hash; session cookies |
| Richer data (Rev 5) | Message DTO adds edited, pinned, album_id, links (incl. hidden text URLs), mentions, reactions, link_preview, views/forwards/replies, service event details; inline poll/location/dice/contact (no phone); endpoints /pinned, /topics, single message, /members (role, search), per-message who-reacted; chat description + members_count. Guard test: no read receipts, listened marks or online status, ever |
| Review (Rev 4) | Claim guard for set-up servers; hashed session IDs; proxy-aware lockout; DO setup password; media cache naming fix; QR success-on-refresh; reconnect + 503 health; per-chat describe fallback; topic+search rejected; video_note kind; SIGTERM shutdown |
| Search (§9.4) | `dialog_id` required in v1; no `searchGlobal` |
| Supply chain (§11 #13, §14) | Compose pins a digest; Render button builds upstream HEAD — documented |
| Docs | Kill switch; account/ban notes; `SECURITY.md` contents |
