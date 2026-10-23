# Native Pushover notifications

Native Pushover is an opt-in integration in **Tools → Pushover**. It does not
require a connector container or CRM. Existing HTTP tools and post-call timing
remain unchanged. The three native adapter names are reserved; rename a
conflicting managed tool before enabling that phase. Other existing Pushover
HTTP tool names remain valid. Summary generation is shared with [in-call HTTP summaries](In-Call-Summaries.md).

## Configure through the Admin UI

1. Register a Pushover application and have a licensed Pushover user or group with
   an active device. Enter its application token. Saved tokens and recipient keys
   are masked; blank credential fields preserve the saved values.
2. Add named recipients, enter each user/group key, and save. **Validate** checks a
   key without sending a notification. **Send test** sends one fixed test message
   only when explicitly clicked. Settings saves and previews never send.
3. Choose the sound (blank respects the user's default), priority and delivery
   timeout. Emergency priority, receipts, attachments and supplementary URLs are
   not supported.
4. Enable notifications for selected agents and assign recipients separately for
   Arrival, Briefing and Completion. New agents are disabled by default. Deleted
   or disabled recipients fail closed; stale assignments never select a fallback.
5. Set each phase's title/body templates, using the preview to check synthetic
   content. Optional summary controls are available for briefing and completion:
   explicit LLM provider, prompt (`{max_words}`), word limit, timeout and failure
   policy. Missing credentials never switch to another summary provider.
6. When migrating an existing connector, disable only its equivalent Pushover sends
   for the pilot agent and retain its CRM/email functions to prevent dual sends.
7. Save, then **Apply settings**. This publishes the normal new-call generation;
   active calls retain their captured scope, settings and credential references.

Arrival runs after the agent's selected pre-call enrichment completes, before
provider startup. Enrichment failure leaves unknown values; arrival failure never
prevents the call. Delivery is bounded by the configured timeout (default 5s),
which is additional potential greeting latency. Use a shorter timeout if needed.

Briefing exposes `pushover_briefing` with an operator-configured `update_key`
allowlist (default `handoff`). Instruct the agent to invoke it **before transfer**.
The operation generates its summary internally, in one tool call. A briefing says
handoff requested; it cannot establish that a transfer succeeded or was answered.
Internal acknowledgements are silent tool data; conversation/tool continuation is
preserved. Completion runs through existing post-call cleanup and describes only
the AVA conversation and trusted outcome, never unobserved human conversation.

## Message fields and identity

Templates support `{agent}`, `{stage}`, `{name}`, `{identity_source}`, `{company}`,
`{crm_status}`, `{caller_number}`, `{callback_number}`, `{destination}`, `{call_id}`,
`{outcome}`, `{language}`, `{meeting}`, `{summary}`, `{summary_status}` and
`{update_key}`. Missing data is unknown. Values are inserted once as plain text;
model/caller data cannot select credentials, recipients, endpoints or policies.
The default message preserves essential fields and shortens the recap to fit.

Name precedence is caller-confirmed correction, CRM identity, usable caller-ID
name, then unknown. Configure existing metadata collection to retain
`customer_name` and `customer_name_source=caller_confirmed` when applicable.
Company may come from `company`, `company_name` or `customer_company`; CRM status
comes from enrichment `crm_status`/`match_status`. A distinct `callback_number`,
`language` and `confirmed_meeting` can be consumed when already captured. Pushover
performs no CRM queries, meeting extraction or CRM writes. A confirmed name
improves presentation without changing identity/routing/consent authority.

Choose an intended transfer destination for an agent if completion suppression is
wanted. Suppression requires an actual transferred outcome, an accepted briefing
to that recipient, a matching configured destination, and the corresponding next
transfer attempt. Unknown/mismatched destinations, failed/cancelled transfer or
uncertain/rejected briefing retain completion. Conservative matching can send a
completion if the PBX exposes a different destination description; this avoids
silently suppressing a necessary notification.

## Delivery and privacy

The sender uses only Pushover's fixed HTTPS API, validates TLS, disables redirects,
bounds responses and waiters, and limits connections to two across engine/Admin
processes through leases in their shared `/app/data/pushover/locks` directory.
Both containers must share the data mount and a compatible runtime UID, as in the
standard Compose deployment. A lease/credential permission failure fails delivery
without affecting call ownership. No generic HTTP request-body tracing is used.

Diagnostics distinguish accepted, rejected, uncertain, capacity/timeout and
skipped/suppressed outcomes, separately from summary provider/model/status/timing.
Accepted means queued by Pushover, not displayed/read/acknowledged. There is no
automatic retry after an ambiguous response, timeout or cancellation. Explicit
API rejections temporarily open a five-minute circuit for that recipient (or all
recipients for a quota rejection), preventing repeated invalid requests. Credential
replacement creates a new reference and permits validation of the corrected key.

Repeated invocations for the same call, update key, latest caller request and
transfer attempt are deduplicated. A later caller request or transfer attempt can
produce a new briefing. There is a 64-event limit per call. Protection is
in-memory and best-effort: there is no durable outbox, restart replay or
exactly-once promise. A process restart loses duplicate state.

Credentials use existing owner-only managed secret files. Replacement creates a
new immutable reference, preserving in-flight call scope. Removing a recipient or
application credential clears its current configuration reference; old files are
retained for captured calls and rollback. Retired files may be removed manually
only after no retained call/configuration references them. Do not copy secret
files into support exports or public PRs.

## API contract and verification

Official API checked October 23, 2026: [Pushover API](https://pushover.net/api).
Messages use `POST /1/messages.json`, recipient validation uses
`POST /1/users/validate.json`. Acceptance requires HTTP 200 and JSON `status=1`.
Titles are limited to 250 characters and bodies to 1,024 Unicode characters.
Quota exhaustion is a rejection, not success. Tests use synthetic conversations
and mocked model/network clients; they send no real notifications.

For live acceptance, configure an authorized test recipient through the UI. Verify
arrival after enrichment, briefing before transfer, AI-handled completion,
failed-transfer completion, duplicate protection, and cleanup. Test calls and
real notification acceptance are separate from mocked automated coverage.
