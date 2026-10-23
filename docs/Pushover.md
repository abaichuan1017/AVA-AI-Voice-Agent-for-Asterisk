# Pushover tools

Pushover is a native tool type in the existing **Tools → Pre-Call / In-Call /
Post-Call** workflow. Create as many named tools as needed, each with its own
recipient, templates, summary settings and description. No separate integration
panel, automatic agent assignments or connector container is required.

## Configure a tool

1. Choose a phase, click **Add Tool**, and select **Pushover** as the tool type.
2. Enter a tool name, for example `notify_sales` or `send_call_recap`. Enable the
   tool and choose whether it is global using the normal controls. Global tools
   apply to all agents except existing opt-outs; otherwise select the tool in
   **Agents**, in that phase's existing tool selector.
3. Give the recipient a display name. Enter a Pushover application token and a
   user/group key. Both are password fields. Blank fields keep saved credentials;
   explicit clear controls remove the current references. Keys are never supplied
   by the AI or included in the tool's model parameters.
4. Set the notification title and plain-text message template. **Preview message**
   uses synthetic data and makes no network/model request. **Validate recipient**
   checks credentials without sending a message. **Send test notification** sends
   a fixed synthetic message only when explicitly clicked.
5. For in-call and post-call tools, optionally enable **Generate AI Summary** and
   choose the configured provider, summary prompt, word limit, timeout and failure
   policy. The same tool generates the summary internally before delivery; there
   is no separate summary-tool invocation or copying step. Explicit provider
   selection never falls back to another endpoint. Pre-call tools have no summary.
6. Click **Update tool**, then the Tools page's normal **Save & Apply**. This stages,
   saves and publishes the same captured generation as HTTP tools. Preview,
   credential staging and tests do not enable or save a tool. Active calls retain
   their captured settings and immutable credential references.

The **tool description / when to use** field is guidance offered to the in-call
AI. Agent instructions can add rules, for example:

> Before transferring to Sales, call `notify_sales`, then proceed with transfer.

The **summary prompt** controls the generated recap, for example:

> Summarize the caller's request, troubleshooting and reason for escalation in
> at most {max_words} words. State observed facts only.

These are separate controls. A description does not schedule pre/post-call tools;
phase selection governs those automatic lifecycle hooks. A prompt cannot guarantee
execution after a transfer. Invoke the notification before transfer when needed.
A booking update or general callback notification can use a different named tool,
description and template; in-call tools are not tied to transfers.

## Phase behavior

- **Pre-call:** selected notifications run after selected enrichment finishes,
  before provider startup. Failure never prevents answering. Delivery adds at most
  its configured timeout to this stage, so use a short budget where appropriate.
- **In-call:** the AI invokes the named tool according to its description and Agent
  prompt. It has no recipient, credential or URL arguments. Internal acknowledgements
  stay silent while the conversation and subsequent tools continue normally.
- **Post-call:** selected tools run through AVA's existing post-call lifecycle.
  They can summarize the finalized AVA conversation and observed outcome. Existing
  post-call timing is unchanged; unobserved human conversations are unavailable.

When migrating another connector, disable only its equivalent notification sends
for the pilot agent and preserve its CRM/email functions to avoid duplicate sends.
The unreleased integration-panel prototype's top-level `pushover` document is
retained for rollback but does not execute or create tool assignments. Configure
named tools through this workflow; no prototype recipient/policy is auto-enabled.

## Templates and trusted data

Templates support `{agent}`, `{context_name}`, `{stage}`, `{name}`, `{caller_name}`,
`{identity_source}`, `{company}`, `{crm_status}`, `{caller_number}`, `{callback_number}`,
`{called_number}`, `{destination}`, `{call_id}`, `{outcome}`, `{call_outcome}`,
`{call_duration}`, `{language}`, `{meeting}`, `{summary}` and `{summary_status}`.
Values are inserted once as plain text; missing data is unknown. Titles are bounded
at 250 characters and messages at 1,024 Unicode characters. The default template
preserves structured details and shortens the recap to fit.

Name precedence is caller-confirmed correction, CRM identity, usable caller-ID name,
then unknown. Existing metadata collection may retain `customer_name` and
`customer_name_source=caller_confirmed`. Company/CRM status and callback/language
are consumed only when already captured. Meeting details require successful
current-call calendar writes; metadata or conversational claims alone do not
establish a booking. Pushover performs no CRM queries or identity/routing changes.

For handoffs, write **handoff requested** in the template rather than claiming a
transfer was answered or successful. Optional **Advanced** controls allow an in-call
tool to identify an intended transfer destination key, and a post-call tool to
reference that tool by name for completion suppression. Suppression requires an
accepted send with the same effective credentials and an actual matching next
transfer outcome. Failed/cancelled transfers, changed credentials, unknown/mismatched
destinations and rejected/uncertain sends retain completion. Leaving the reference
blank always retains the normal post-call notification.

## Delivery, credentials and diagnostics

The shared sender uses Pushover's fixed HTTPS endpoint with TLS verification,
redirects disabled, bounded responses/waiters and at most two shared connections.
Engine/Admin need the same data mount and compatible UID for leases in
`/app/data/pushover/locks`, as in the standard Compose deployment. Failures never
block answering, transfer, hangup or cleanup. Generation and delivery have separate
diagnostics and a combined outer timeout budget.

Credentials are staged in immutable owner-only managed secret files. Normal tool
configuration and API responses contain references, never plaintext keys. Replaced,
cleared or abandoned draft references may leave retired files for captured calls
and rollback. Remove them manually only after no retained configuration/call uses
them. Never copy secret files into exports or public PRs.

Diagnostics distinguish accepted, rejected, uncertain, timeout, capacity, skipped
and suppressed results. Acceptance means API queuing, not phone display or operator
acknowledgement. There is no automatic retry after uncertain delivery/cancellation.
Explicit rejections open a temporary five-minute circuit. Duplicate protection is
per call and named tool, with later caller turns/transfer attempts allowing a new
update and a 64-event call limit. Different named tools remain independent.
Protection is best effort and in-memory; restart replay, exactly-once delivery,
emergency acknowledgement workflows and attachments are not implemented.

Official API checked October 23, 2026: [Pushover API](https://pushover.net/api).
Sending uses `POST /1/messages.json`, validation uses `POST /1/users/validate.json`;
acceptance requires HTTP 200 and JSON integer `status=1`. Automated verification
uses synthetic conversations and mocked clients. Live acceptance requires an
operator-configured recipient and calls testing chosen phases, summary/provider
failure, duplicate handling, transfer outcomes, continuation and cleanup.
