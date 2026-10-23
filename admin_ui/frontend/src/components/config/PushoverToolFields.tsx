import { useState } from 'react';
import axios from 'axios';
import { FormInput, FormSelect, FormLabel } from '../ui/FormComponents';

import { pushoverDocument, type PushoverDraft } from './pushoverToolConfig';

export function PushoverToolFields({
    value,
    onChange,
    credentials,
    onCredentialsChange,
}: {
    value: PushoverDraft;
    onChange: (value: PushoverDraft) => void;
    credentials: { token: string; recipient: string };
    onCredentialsChange: (value: { token: string; recipient: string }) => void;
}) {
    const [status, setStatus] = useState('');
    const [preview, setPreview] = useState<{ title: string; message: string } | null>(null);
    const [busy, setBusy] = useState(false);
    const change = (key: string, next: unknown) => {
        onChange({ ...value, [key]: next });
        setPreview(null);
    };
    const action = async (kind: 'preview' | 'validate' | 'test') => {
        setBusy(true);
        setStatus('');
        try {
            const response = await axios.post(`/api/pushover/${kind}`, {
                config: pushoverDocument(value),
                ...(kind === 'preview'
                    ? {}
                    : {
                          credentials: Object.fromEntries(
                              Object.entries(credentials).filter(([, v]) => Boolean(v))
                          ),
                      }),
            });
            if (kind === 'preview') setPreview(response.data);
            else {
                onChange({ ...value, ...response.data.credential_refs });
                onCredentialsChange({ token: '', recipient: '' });
                setStatus(
                    `${kind === 'test' ? 'Test' : 'Validation'}: ${response.data.delivery_status}${response.data.error_code ? ` (${response.data.error_code})` : ''}. Acceptance does not prove display or acknowledgement.`
                );
            }
        } catch {
            setStatus(
                'Unable to complete this action. Check credential format and configuration. No automatic retry will be made.'
            );
        } finally {
            setBusy(false);
        }
    };
    return (
        <fieldset disabled={busy} className="min-w-0 space-y-3">
            {value.phase === 'in_call' && (
                <div>
                    <FormLabel
                        htmlFor="pushover-description"
                        tooltip="The AI sees this description. Explain when to invoke this named tool; the agent prompt can add instructions."
                    >
                        Tool description / when to use
                    </FormLabel>
                    <textarea
                        id="pushover-description"
                        className="w-full rounded border border-input bg-background p-3"
                        value={value.description || ''}
                        onChange={e => change('description', e.target.value)}
                        placeholder="Notify Sales before transferring a caller to Sales."
                    />
                </div>
            )}
            <FormInput
                label="Recipient name"
                value={value.recipient_label || ''}
                onChange={e => change('recipient_label', e.target.value)}
                placeholder="e.g., Sales team"
            />
            <div className="grid gap-4 md:grid-cols-2">
                <FormInput
                    label="Application token"
                    type="password"
                    autoComplete="new-password"
                    value={credentials.token}
                    onChange={e => onCredentialsChange({ ...credentials, token: e.target.value })}
                    placeholder={
                        value.token_file ? 'Saved — blank keeps it' : 'Enter application token'
                    }
                    tooltip="Stored in an owner-only secret file when you update the tool. It is never returned as plaintext."
                />
                <FormInput
                    label="User/group key"
                    type="password"
                    autoComplete="new-password"
                    value={credentials.recipient}
                    onChange={e =>
                        onCredentialsChange({ ...credentials, recipient: e.target.value })
                    }
                    placeholder={value.key_file ? 'Saved — blank keeps it' : 'Enter user/group key'}
                />
            </div>
            <div className="flex gap-4 text-xs">
                {value.token_file && (
                    <button type="button" onClick={() => change('token_file', '')}>
                        Clear saved application token
                    </button>
                )}
                {value.key_file && (
                    <button type="button" onClick={() => change('key_file', '')}>
                        Clear saved recipient key
                    </button>
                )}
            </div>
            <FormInput
                label="Notification title"
                value={value.title || ''}
                onChange={e => change('title', e.target.value)}
                placeholder="{agent}: call update"
            />
            <div>
                <FormLabel
                    htmlFor="pushover-body"
                    tooltip="Plain-text templates insert trusted call values once. The AI cannot choose credentials, recipients or the endpoint."
                >
                    Message template
                </FormLabel>
                <textarea
                    id="pushover-body"
                    className="w-full min-h-32 rounded border border-input bg-background p-3 font-mono text-sm"
                    value={value.body || ''}
                    onChange={e => change('body', e.target.value)}
                    placeholder="{name} | {caller_number}&#10;Summary ({summary_status}): {summary}&#10;Call: {call_id}"
                />
                <p className="text-xs text-muted-foreground">
                    Variables:{' '}
                    {
                        '{agent}, {name}, {company}, {crm_status}, {caller_number}, {callback_number}, {called_number}, {destination}, {call_id}, {outcome}, {language}, {meeting}, {summary}, {summary_status}'
                    }
                    . Preview uses synthetic data only.
                </p>
            </div>
            <div className="grid gap-4 md:grid-cols-3">
                <FormSelect
                    label="Priority"
                    value={String(value.priority ?? 0)}
                    options={[
                        { value: '-2', label: 'Lowest' },
                        { value: '-1', label: 'Low' },
                        { value: '0', label: 'Normal' },
                        { value: '1', label: 'High' },
                    ]}
                    onChange={e => change('priority', Number(e.target.value))}
                />
                <FormInput
                    label="Sound"
                    value={value.sound || ''}
                    onChange={e => change('sound', e.target.value)}
                    placeholder="Default"
                />
                <FormInput
                    label="Delivery timeout (ms)"
                    type="number"
                    min={100}
                    max={10000}
                    value={value.timeout_ms || 5000}
                    onChange={e => change('timeout_ms', Number(e.target.value))}
                />
            </div>
            <details>
                <summary className="cursor-pointer text-sm">
                    Advanced: transfer completion suppression
                </summary>
                <p className="text-xs text-muted-foreground my-3">
                    Optional. An in-call tool can identify its intended transfer destination. A
                    post-call tool may suppress only after that named tool was accepted for the same
                    credentials and the matching next transfer actually succeeded. Failed/cancelled
                    transfers retain completion.
                </p>
                {value.phase === 'in_call' && (
                    <FormInput
                        label="Intended transfer destination key"
                        value={value.destination_key || ''}
                        onChange={e => change('destination_key', e.target.value)}
                    />
                )}
                {value.phase === 'post_call' && (
                    <FormInput
                        label="Suppress after accepted in-call tool"
                        value={value.suppress_after_tool || ''}
                        onChange={e => change('suppress_after_tool', e.target.value)}
                        placeholder="e.g., notify_sales"
                    />
                )}
            </details>
            <div className="flex flex-wrap gap-3">
                <button
                    type="button"
                    className="rounded border px-3 py-2 text-sm"
                    onClick={() => void action('preview')}
                >
                    Preview message
                </button>
                <button
                    type="button"
                    className="rounded border px-3 py-2 text-sm"
                    onClick={() => void action('validate')}
                >
                    Validate recipient
                </button>
                <button
                    type="button"
                    className="rounded border px-3 py-2 text-sm"
                    onClick={() => void action('test')}
                >
                    Send test notification
                </button>
            </div>
            {preview && (
                <div className="rounded border p-3">
                    <p>{preview.title}</p>
                    <pre className="whitespace-pre-wrap text-sm">{preview.message}</pre>
                    <p className="text-xs">
                        Synthetic preview only. No notification or model request was sent.
                    </p>
                </div>
            )}
            {status && (
                <p role="status" className="text-sm">
                    {status}
                </p>
            )}
            <p className="text-xs text-muted-foreground">
                Update tool, then Save &amp; Apply on the Tools page. Agent access uses the existing
                tool selectors. Pre/post-call tools run automatically when selected; in-call tools
                are invoked by the AI. Best-effort delivery has no restart replay.
            </p>
        </fieldset>
    );
}
