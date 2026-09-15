# DamDM by DammAI

Automatically send a private Instagram reply when someone comments a keyword on a
creator's post or Reel — with an optional follow-gate that verifies the follow
through Meta's API before delivering a link, automated public replies ("Sent! Check
your DM"), DM keyword automations, and a stored inbox of every contact, comment and
message.

Instagram is the only live platform. The schema carries a `platform` column so
WhatsApp and Telegram can be added later without a parallel set of tables.

Built on Instagram's official Graph APIs and OAuth. **No passwords are ever
collected, and there is no scraping or browser automation anywhere in this codebase.**

> DamDM is an independent product of DammAI. It is not affiliated with, endorsed by,
> or sponsored by Instagram or Meta.

---

## Status

**Phase 1 complete** — project skeleton, custom user model, authentication, the full
data model (contacts, inbox, multi-step automations, runs), admin, migrations, plan
seeding and the test suite.

| Phase | Scope | State |
|---|---|---|
| 1 | Project, models, auth, admin, migrations, tests | ✅ Done |
| 2 | Sign-in popup (email, Google, Instagram), Instagram connect, account management, post sync, token refresh, Meta callbacks | ✅ Done |
| 3 | Webhooks, contacts/inbox ingest, keyword processing, private replies | ⏳ Next |
| 4 | Automation builder UI ✅; step runner: public replies, follow gate, link delivery, tags, analytics | 🚧 Builder done |
| 5 | ₹99/month plan, 14-day trial with UPI AutoPay (Razorpay), trial-abuse checks, dark mode | ✅ Done |

## Stack

Python 3.12 · Django 5.1 · DRF · PostgreSQL (SQLite for local dev) · Redis · Celery +
Beat · Django templates + HTMX · plain CSS (no build step) · `httpx` · `cryptography` ·
Razorpay · pytest.

## Applications

```
apps/
├── core/         Shared base models, encryption, audit log, data-deletion requests
├── accounts/     Users, sign-in popup, Google sign-in, social logins, OAuth state
├── instagram/    Instagram OAuth, account management, Graph client, post sync, callbacks
├── automations/  Automation builder, templates, keywords, posts, steps, runs, matching
├── messaging/    Contacts, tags, comments, conversations, messages, follow checks
├── analytics/    Tracked links, clicks, daily rollups
├── billing/      Plan, Razorpay subscriptions, free trial and abuse checks, payments
└── referrals/    Refer and earn: invite codes, rewards, UPI payouts
```

## Sign-in and connecting Instagram

Signed-out visitors get one popup on every page (`templates/accounts/partials/auth_panel.html`)
with every way in. The same panel appears inline at `/accounts/login/` and
`/accounts/register/` for no-JS visitors and `LoginRequired` redirects. Forms post over
HTMX, so errors appear inside the popup. Add `?auth=signin|signup` to any URL to open it
on load; `?next=` is kept through every method.

| Method | What happens |
|---|---|
| **Email: sign up** | Email and the password twice. No confirmation email. |
| **Email: sign in** | Email and password. |
| **Continue with Google** | OAuth → userinfo. Creates a passwordless user with `email_verified=True`, or signs in the linked one (`SocialLogin`). Google emails that aren't verified are refused. |
| **Continue with Instagram** | Instagram Login OAuth. Signs in the user who holds that Instagram account. For an account DamDM has never seen, the identity is parked in the session (token encrypted, 15 minutes) and the popup asks for **just an email**. Signing in or signing up by any method instead connects the parked account to that user. |
| **Connect Instagram** (signed in) | The same Instagram flow, attached to the current user (dashboard button). |

**One Instagram account belongs to one DamDM account.** `connect_account`
(`apps/instagram/services/connect.py`) refuses an account someone else holds, and a
partial unique constraint on `ConnectedAccount` backs that up in the database.

Mock mode (`META_MOCK=True`, `GOOGLE_MOCK=True`, the dev defaults) skips the providers
and signs in fixed fake identities (`mock.google.user@example.com`, `@mock_creator`).
`config.settings.prod` refuses to start with either enabled.

### Real Google and Instagram sign-in

Set these in `.env`. The comments in `.env.example` repeat the steps.

| Variable | Where it comes from |
|---|---|
| `APP_BASE_URL` | The app's public URL. **Instagram only redirects to HTTPS**, so in development run a tunnel (`ngrok http 8000` or `cloudflared tunnel --url http://localhost:8000`) and use its `https://` URL. Dev settings add its host to `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`. |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | Google Cloud Console → APIs & Services → **OAuth consent screen** (External, add yourself as a test user) → **Credentials** → Create OAuth client ID → *Web application*. |
| `META_APP_ID`, `META_APP_SECRET` | developers.facebook.com → your app (type *Business*) → **Instagram** → *API setup with Instagram business login*. Use the **Instagram** app ID and secret, not the Facebook ones. |
| `GOOGLE_MOCK`, `META_MOCK` | `False` to use the real providers. |
| `GOOGLE_REDIRECT_URI`, `META_REDIRECT_URI` | Leave empty; they are derived from `APP_BASE_URL`. |

Register the redirect URIs exactly (with the trailing slash):

- Google OAuth client → Authorised redirect URIs: `<APP_BASE_URL>/accounts/google/callback/`
- Instagram → *Set up Instagram business login* → Business login settings → OAuth
  redirect URIs: `<APP_BASE_URL>/instagram/callback/`

While the Meta app is in development mode, only Instagram accounts added under
**App roles → Roles → Instagram testers** can sign in. Each tester accepts the invite in
Instagram → Settings → Website permissions → Apps and websites → Tester invites. The
account must be a Business or Creator account.

The tunnel URL changes each time a free ngrok session restarts (unless you claim its free
static domain). When it changes, update `APP_BASE_URL` and both redirect URIs.

To run without Redis while developing, set `CELERY_TASK_ALWAYS_EAGER=True` in `.env`:
tasks then run inline in the request (connecting an account waits for its first sync).
With eager mode off and no broker, pages still work, but "Sync now" reports that
background jobs aren't running.

## Instagram accounts

`/instagram/accounts/` lists the user's connected accounts: status, followers, posts
synced, last sync, token expiry and the last API error. Each has **Sync now** (throttled
to once a minute), **Disconnect** (drops the token and pauses the account's automations),
and **Reconnect** when the token has expired or been revoked. Reconnecting reuses the
earlier row, so posts and contacts are kept. The account page shows the synced posts and
Reels.

All Graph API calls go through `apps/instagram/services/graph.py`, which classifies
errors: token errors mark the account *expired*, permission errors mark it *error*, and
network, 5xx and rate-limit errors are retried.

### Scheduled jobs

Defined in `CELERY_BEAT_SCHEDULE` (`config/settings/base.py`) and loaded into
django-celery-beat when `celery beat` starts.

| Job | When | What it does |
|---|---|---|
| `refresh_expiring_tokens` | every 6 hours | Refreshes tokens within 7 days of expiry (tokens last 60 days) and marks lapsed ones expired |
| `sync_stale_accounts` | hourly | Syncs profile and up to 500 recent posts for accounts not synced in 6 hours |
| `process_pending_data_deletions` | hourly | Re-queues deletion requests that were never processed |
| `purge_webhook_payloads` | daily 03:00 | Blanks raw webhook payloads older than 30 days |

A newly connected account is synced right away.

### Meta callbacks

In the Meta app dashboard (Instagram → *API setup with Instagram business login* → *Set up
Instagram business login*), set:

| Setting | URL |
|---|---|
| Deauthorize callback URL | `<APP_BASE_URL>/instagram/deauthorize/` |
| Data deletion request URL | `<APP_BASE_URL>/instagram/data-deletion/` |

Both verify Meta's `signed_request` with `META_APP_SECRET`; anything that fails is rejected
with 400 and audited. **Deauthorize** marks the account *revoked*, drops its token and
pauses its automations. **Data deletion** returns `{"url", "confirmation_code"}` right
away, then a Celery task deletes every connection to that Instagram account (with its
posts, contacts, comments, messages and automations), the person's contact records on
other accounts, and their Instagram sign-in identity. The DamDM login of whoever
connected the account is kept. The status page lives at `/data-deletion/<code>/`.

## Automations

`/automations/` is where users set up everything the product does.

1. **New automation** opens a gallery of starting points (`services/recipes.py`): send a
   link on comment, follow to unlock, DM keyword reply, story reply, story mention, or
   start from scratch.
2. **The builder** is one page of numbered steps that show only when they apply:
   *When someone…* (comments / DMs / replies to a story / mentions you) → *On which post?*
   (any, specific, next) → *keywords* (chips, or "any") → *public reply* (rotating
   variants) → *who gets it* (everyone, only followers, or ask to follow first) → *DM* (text and a link button) → *settings*. A live
   Instagram-style phone preview on the right updates as the user types.
3. **The list** shows each automation as a plain-English summary ("Someone comments “LINK”
   on 2 posts") with its steps, the number of people reached, a Live/Paused toggle, Edit and
   Delete.

`AutomationBuilderForm` validates the friendly fields, and `services/builder.py` maps them
onto `Automation`, `AutomationKeyword`, `AutomationPost` and `AutomationStep`. Editing
updates steps in place (matched by type), so people partway through a flow keep their
place. Turning an automation on requires a connected account; otherwise it's saved as a
draft.

Automations only start replying once webhooks (phase 3) and the step runner (phase 4)
are built.

## Plans, free trial and billing

There is one plan, **Pro: ₹99/month**, with unlimited DMs and automations
(`seed_plans` creates it and retires any older plans). Payments go through **Razorpay
subscriptions** using **UPI AutoPay** (cards and netbanking also work).

**Trial.** New customers get **14 days free**, but they approve the AutoPay mandate when
they start, so nothing is charged on day one. Razorpay takes the first ₹99 on day 15 and
then every month. Cancelling during the trial stops the first charge; access continues
until the trial ends. Automations can only be turned on with a live trial or plan, and
they are paused automatically when access ends.

**One trial per person.** `apps/billing/services/trials.py` refuses a second trial when
any of these has had one before:

| Signal | Why |
|---|---|
| Instagram account (every one the user ever connected) | The thing being automated; can't be faked cheaply |
| Google account (`sub`) | Survives email changes |
| Email, with Gmail dots and `+tags` removed | `ravish.kumar+2@gmail.com` = `ravishkumar@gmail.com` |
| Browser (`damdm_device` cookie) | Stops one browser farming trials |
| UPI ID or card behind the mandate | Checked when Razorpay confirms the mandate; a repeat revokes the new trial at once |

Values are stored only as HMAC hashes (`TRIAL_FINGERPRINT_KEY`) in `TrialFingerprint`, and
`TrialClaim` rows survive account deletion, so deleting and signing up again doesn't
reset anything. **IP addresses are recorded but never block a trial**: mobile networks
put thousands of people behind one IP.

**Monthly tracking.** Razorpay charges the mandate each month and sends webhooks to
`/billing/webhooks/razorpay/`. They are verified with `RAZORPAY_WEBHOOK_SECRET`, stored
once in `BillingWebhookEvent`, and applied:

| Razorpay event | DamDM |
|---|---|
| `subscription.authenticated` | Trial starts (mandate approved) |
| `subscription.charged` | Period dates updated, `Payment` recorded, trial marked converted |
| `subscription.pending` | *Payment due*: access kept while Razorpay retries |
| `subscription.halted` / `completed` / `cancelled` | Access ends, automations paused |

`sync_subscriptions` (hourly) re-reads any subscription whose trial or month has ended,
in case a webhook was missed. The browser's checkout result is never trusted on its own:
its signature is verified, then the subscription is fetched from Razorpay.

**Razorpay setup.** Create API keys, a ₹99 monthly plan (`RAZORPAY_PLAN_ID`) and a webhook
for the events above; see `.env.example`. With `RAZORPAY_MOCK=True` (the dev default)
checkout approves instantly and trials renew in `sync_subscriptions`, with no Razorpay
account needed. Production refuses to start in mock mode.

## Refer and earn

Every user gets an invite link, `/r/<CODE>/`, on **Refer & earn** (sidebar, Settings and a
dashboard card), with Copy, WhatsApp and native share buttons. It lives in `apps/referrals`.

1. **Invite.** Opening the link stores the code in a signed cookie for 30 days and opens the
   sign-up popup ("A friend invited you"). Only an account **created within 30 minutes** of
   signing in (email, Google or Instagram) is attached, so existing users can't be claimed.
2. **Earn.** When the friend's **first real payment** succeeds (not the free trial), the
   referrer earns **50% of it (₹49.50)**, once per friend (`ReferralReward` is one-to-one
   with `Referral`, and `Referral.referred` is one-to-one with the user).
3. **Hold.** Rewards stay *on hold* for 7 days; `release_referral_rewards` (hourly) makes
   them *available*, or cancels them if the payment was refunded.
4. **Withdraw.** From ₹100, the user enters a UPI ID (stored encrypted) to request a
   `Payout`. One open withdrawal at a time. In the admin, pay the UPI ID shown, put the UTR
   in *Reference*, then run **Mark as paid**; **Reject** returns the money to the wallet.

**Self-referrals don't earn.** The same hashed signals as the trial are compared between
referrer and friend: email (Gmail dots and `+tags` ignored), Google account, Instagram
accounts, browser (including every browser the referrer opened their referral page in),
and UPI ID or card (`Payment.method_hash`). They're checked when the friend signs up and
again when they pay; a match marks the referral *Not eligible*.

## Coupons

Coupons live in `apps/coupons` and are created in **Admin → Coupons**:

| Field | Meaning |
|---|---|
| Instagram usernames | One per line (`@` optional). **Only accounts whose connected Instagram username exactly matches** can use the coupon, once per username and once per DamDM user. |
| Discount % | 1–100. |
| Discounted months | How many monthly payments get the discount. |
| Razorpay offer id | Needed for discounts **under 100%**: create an Offer in the Razorpay Dashboard with the same percentage, limited to the same number of cycles, and paste its `offer_…` id. |
| Expires | The coupon can't be used after this. |
| Notify on connect | Shows a dashboard notification ("🎁 You have a coupon") when that Instagram account connects, or straight away if it's already connected. |

**Using a coupon.** The holder sees it on **Plan & billing** and taps **Redeem** when starting
their plan. A **100% coupon** adds free months after the 14-day trial by moving the first AutoPay
charge later (14 days + 1 month = first ₹99 on day 45); no Razorpay setup needed. A smaller discount
is applied by attaching the Razorpay Offer to the subscription. Coupons are used when starting a plan,
not on a plan that's already running.

**Archiving.** Select coupons and run **Archive**. Nobody can redeem them afterwards. Archiving a
**100% coupon also cancels the subscriptions it started** (automations pause and the user is
notified); subscriptions from **discount coupons keep running**. Whether a holder still follows
@damdm is checked manually; archive the coupon if not.

## Dark mode

The whole site follows the device's light/dark setting. People can override it with the
moon/sun button (header, sidebar, top bar) or in Settings → Appearance
(System/Light/Dark); the choice is saved in the browser and applied before the page
paints. Colours are CSS variables, redefined under `[data-theme="dark"]`; Instagram
mock-ups stay light on purpose.

## Data model

```
User ─┬─ SocialLogin                    (Continue with Google / Instagram)
      ├─ Subscription ─ Plan
      └─ ConnectedAccount               (instagram today; whatsapp/telegram later)
            ├─ Post
            ├─ WebhookEvent
            ├─ Tag
            ├─ Contact ─┬─ ContactTag ─ Tag
            │           ├─ Comment      (audience comments + owner/automated replies)
            │           ├─ Conversation ─ Message   (every DM, both directions)
            │           ├─ FollowCheck
            │           └─ AutomationRun
            └─ Automation ─┬─ AutomationKeyword
                           ├─ AutomationPost ─ Post
                           ├─ AutomationStep
                           └─ AutomationRun ─┬─ Message / FollowCheck / LinkClick
                                             └─ trigger: Comment or Message
```

`Contact` is the hub for third-party personal data: deleting one cascades to that
person's comments, conversation, messages, tags, follow checks and runs, which is
what the platform data-deletion callback relies on.

### accounts

| Model | Purpose | Key fields |
|---|---|---|
| `User` | A DamDM customer. Email is the login id. Google and Instagram-only users have no usable password. | `email`, `email_verified` (true only once Google proves the address), `full_name`, `avatar_url`, `timezone`, `onboarding_state` |
| `SocialLogin` | A third-party identity used to sign in. Proves identity only — never holds a messaging token. | `provider` (google/instagram/facebook), `provider_uid` (unique per provider), `email`, `extra_data` |
| `OAuthState` | One-time `state` for sign-in and connect flows. `connect` states must be bound to a user. | `provider`, `purpose` (login/connect), `user`, `expires_at`, `consumed_at` |

### instagram (connected channels)

| Model | Purpose | Key fields |
|---|---|---|
| `ConnectedAccount` | A messaging account a user connected. One DamDM user may claim a platform account at a time, so webhooks always route to one owner. | `platform`, `external_account_id`, `username`, `account_type`, `followers_count`, `encrypted_access_token`, `token_expires_at`, `scopes`, `status`, `webhooks_subscribed` |
| `Post` | A post, Reel or story, synced for the "pick a post" screen. | `external_media_id`, `media_type`, `product_type`, `caption`, `permalink`, `comments_count` |
| `WebhookEvent` | Raw webhook envelope, stored idempotently before processing. Payloads are blanked after 30 days (`purge_old_payloads`). | `platform` + `external_event_id` (unique), `event_type`, `account`, `payload`, `processing_status` |

### messaging (audience data)

| Model | Purpose | Key fields |
|---|---|---|
| `Contact` | One person who interacted with an account — the CRM record. | `external_user_id` (IGSID, unique per account), `username`, `name`, `is_following`, `source`, `last_interaction_at`, `last_inbound_at` (messaging window), `email`, `phone`, `custom_fields`, `opted_out_at`, `is_blocked` |
| `Tag` / `ContactTag` | Segments such as "lead" or "clicked link"; `added_by_run` records automated tagging. | `name`, `color` |
| `Comment` | Every comment on the account's posts, including the owner's and DamDM's public replies. At most one automated public reply per comment (DB constraint). | `external_comment_id`, `post`, `contact`, `parent`, `text`, `is_from_owner`, `is_automated_reply`, `automation`, `public_replied_at`, `private_replied_at` |
| `Conversation` | The DM thread with one contact. `mode=human` pauses automations while the owner chats. | `contact` (one-to-one), `mode`, `last_message_at`, `unread_count`, `is_archived` |
| `Message` | Every DM in both directions; outbound rows are also the delivery outbox. At most one private reply per comment (DB constraint). | `direction`, `sender_type`, `message_type`, `external_message_id`, `text`, `attachments`, `postback_payload`, `comment`, `automation_run`, `step`, `status`, `attempt_count` |
| `FollowCheck` | Result of the follow-status API. A tap on "I've followed" is never proof. | `contact`, `automation_run`, `is_following`, `api_error` |

### automations (configuration and execution)

| Model | Purpose | Key fields |
|---|---|---|
| `Automation` | What sets an automation off and the re-trigger rules. | `trigger_type` (comment/dm_keyword/story_reply/story_mention), `post_scope` (all/specific/next), `priority`, `once_per_contact`, `cooldown_minutes`, `starts_at`, `ends_at`, `status` |
| `AutomationKeyword` | One keyword rule; the automation matches if any rule does. | `keyword`, `match_type` (exact/contains/whole_word/any), `case_sensitive` |
| `AutomationPost` | Target posts when `post_scope` is specific. | `automation`, `post` |
| `AutomationStep` | One ordered action. Settings live in `config` JSON, validated per type in `clean()`. | `order`, `step_type` (public_reply/send_dm/ask_to_follow/wait_for_reply/collect_email/add_tag/delay/handoff), `config` |
| `AutomationRun` | One contact's pass through one automation. Only one live run per automation and contact (DB constraint); a contact can be in several automations at once. | `contact`, `trigger_comment`/`trigger_message`, `current_step`, `status`, `follow_check_count`, `context`, `expires_at` |

Example step configs:

```jsonc
{"variants": ["Sent! Check your DM 📩", "Link sent, check DMs!"]}        // public_reply
{"text": "Hey! Here's the link", "buttons": [{"title": "Open", "url": "https://..."}]}  // send_dm
{"prompt": "Follow first, then tap below", "button": "I've followed",
 "not_following_text": "Not seeing a follow yet...", "max_checks": 3}    // ask_to_follow
```

### analytics, billing, core

| Model | Purpose |
|---|---|
| `TrackedLink` / `LinkClick` | Short links with click tracking, attributed to a step, contact and run. IPs are stored only as salted hashes. |
| `DailyStat` | Nightly per-account and per-automation rollup (comments, runs, DMs sent/failed, follows, clicks, new contacts) so dashboards never scan `Message`. |
| `Plan` / `Subscription` / `UsageRecord` / `BillingWebhookEvent` | Tiers, provider subscriptions, per-period usage counters, idempotent billing webhooks. |
| `AuditLog` | Append-only record of security-relevant actions, without payloads or secrets. |
| `DataDeletionRequest` | Platform data-deletion callbacks; the unguessable `confirmation_code` is returned to Meta and shown on the status page. |

### Data retention

- Contact, comment and message rows are kept while the account stays connected.
- Raw `WebhookEvent` payloads are blanked after 30 days.
- A platform data-deletion callback deletes the matching `Contact` rows (and everything
  cascading from them) and records only counts on `DataDeletionRequest`.
- Deleting a DamDM account drops every token first, then cascades all of the above.
- Webhooks only deliver messages from after an account connects; older history needs
  an explicit Conversations API sync.

## Getting started

```bash
# 1. Virtualenv (Python 3.12+)
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt

# 2. Environment
cp .env.example .env
python manage.py generate_keys      # paste the output into .env

# 3. Database
python manage.py migrate
python manage.py seed_plans         # creates the ₹99/month Pro plan
python manage.py createsuperuser

# 4. Run
python manage.py runserver
```

Open http://localhost:8000 — admin lives at `/admin/`.

Styling is hand-written CSS in `static/css/app.css`. There is no Node, npm or CSS
build step — edit the file and reload.

### Background workers

Post syncs, token refreshes and data deletions run in Celery. Once Redis is running
(`brew services start redis`):

```bash
celery -A config worker -l info
celery -A config beat -l info
```

### Switching to PostgreSQL

Set `DATABASE_URL` in `.env` and re-run `migrate`:

```
DATABASE_URL=postgres://damdm:damdm@localhost:5432/damdm
```

## Testing

```bash
pytest                    # no network access
pytest --cov              # with coverage
ruff check .              # lint
python manage.py check --deploy   # with DJANGO_SETTINGS_MODULE=config.settings.prod
```

Tests run against in-memory SQLite with `META_MOCK=True`. **No test makes a real
network call** — Meta, Google and Razorpay are always mocked.

## Settings layout

| Module | Use |
|---|---|
| `config.settings.base` | Shared configuration; reads everything from the environment |
| `config.settings.dev` | Local development (default for `manage.py`) |
| `config.settings.test` | pytest: in-memory DB, eager Celery, fast hashing |
| `config.settings.prod` | HTTPS redirect, HSTS, secure cookies, strict hosts |

## Security notes

- **Access tokens** are encrypted with `TOKEN_ENCRYPTION_KEY` — deliberately separate
  from `SECRET_KEY`. They are excluded from admin forms, serializers and logs, and a
  logging filter scrubs anything token-shaped that slips into a log line.
- **OAuth state** is a single-use, expiring value pinned to the browser session that
  started the flow (and, for connect, to the signed-in user), so a callback URL opened in
  another browser can neither sign it in nor attach an account to the wrong user.
- **Emails are never confirmed by DamDM.** So an email address can't be claimed ahead of
  its owner, signing in with Google to an unconfirmed password account turns that password
  off (ending its sessions). A first-time Instagram sign-in can't take over an existing
  email; the person must sign in to that account instead.
- **Webhooks** (Meta and Razorpay) are stored idempotently and only after the raw-body
  signature verifies.
- **One private reply and one automated public reply per comment**, and **one live
  automation run per contact**, are enforced by database constraints, not just app logic.
- **Opted-out or blocked contacts** (`Contact.can_be_automated`) must never be messaged
  by an automation.
- **IP addresses are never stored** — link clicks keep only a salted SHA-256 hash.
- **Tokens stay out of logs**: the log filter also scrubs non-string arguments (httpx logs
  request URLs, which carry `access_token`), and the `httpx`/`httpcore` loggers are
  limited to warnings.
- **Ownership** is enforced through `apps.core.mixins`; every object-level view goes
  through it.
- **Audit log** records security-relevant actions with no payloads or secrets.

## Environment variables

See `.env.example`. Never commit `.env`.

`META_GRAPH_API_VERSION` is environment-driven so the Graph version is never pinned
to something stale in code.

## Deferred decisions

Tracked, not forgotten — these are settled before the phase that needs them:

- Billing period for free accounts is the **calendar month**; paid accounts will use
  the provider's period once Phase 5 lands.
- Follow-gate **"Check Again" rate cap** (`AutomationRun.follow_check_count` and the
  step's `max_checks` exist; the limit is not yet enforced).
- `Plan.instagram_account_limit` is still Instagram-named; generalise it to all
  connected accounts when a second platform ships.
- `LinkClick.recipient_scoped_id` comes from the link's query string and is unverified;
  resolve `contact` / `automation_run` from a signed token in Phase 4.
- `Plan.instagram_account_limit` is 1 for Pro, but the limit isn't enforced yet.
