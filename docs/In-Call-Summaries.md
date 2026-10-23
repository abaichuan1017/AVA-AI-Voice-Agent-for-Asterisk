# In-call HTTP summaries

In Tools → In-call HTTP, enable **Generate AI Summary**, select a configured LLM,
and set the summary prompt, maximum words and timeout. Only that provider receives
the transcript. The prompt supports `{max_words}`. Saving these tools uses the
normal Save & Apply generation: active calls keep their captured tool settings.

Use `"summary": {summary_json}` in a JSON body, or `"summary": "{summary}"`.
Add `"summary_status": "{summary_status}"` and optionally
`"summary_error_code": "{summary_error_code}"`. Generated fields are reserved;
model arguments and enrichment cannot supply their values. Templates retain the
existing single-pass escaping and JSON validation.

The summary describes the AVA conversation so far, including the latest available
transcription. It does not describe a human conversation after transfer. Invoke
the tool **before** transferring; a prompt cannot guarantee an after-transfer hook.
Post-call timing and legacy post-call summary configurations are unchanged.

Summary generation and HTTP delivery have separate configured budgets; their sum
bounds the tool. Cancellation propagates to the summary job. Cleanup detected
before delivery prevents sending. A failure defaults to sending other details
with an empty summary and explicit unavailable status; choose **Do not send the
request** when a summary is required. Generation diagnostics are recorded separately
from delivery status in Call History. An HTTP timeout may leave delivery uncertain;
applications creating tickets should use their own idempotency key such as call ID.

Internal acknowledgements remain tool data, with conversation/tool continuation.
The operator's agent instructions should say when to invoke the tool and what to
say naturally after success or failure. Do not put notification acknowledgements
into the voice script.
