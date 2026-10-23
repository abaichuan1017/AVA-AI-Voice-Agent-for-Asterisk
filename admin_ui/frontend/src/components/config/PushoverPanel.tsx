import { useEffect, useState } from 'react';
import axios from 'axios';
import { FormInput, FormLabel, FormSelect, FormSwitch } from '../ui/FormComponents';

const phases = ['pre_call', 'in_call', 'post_call'] as const;
type Phase = (typeof phases)[number];
const phaseNames: Record<Phase, string> = {
    pre_call: 'Arrival',
    in_call: 'Briefing',
    post_call: 'Completion',
};
type SummarySettings = {
    title: string;
    body: string;
    generate_summary: boolean;
    summary_provider?: string | null;
    summary_prompt: string;
    summary_max_words: number;
    summary_timeout_ms: number;
    summary_failure_policy: 'continue' | 'abort';
};
type Recipient = { label: string; enabled: boolean; key_configured?: boolean };
type Assignment = {
    enabled: boolean;
    pre_call: string[];
    in_call: string[];
    post_call: string[];
    destination_key: string;
    update_keys: string[];
};
type Config = {
    enabled: boolean;
    token_configured?: boolean;
    recipients: Record<string, Recipient>;
    agents: Record<string, Assignment>;
    phases: Record<Phase, SummarySettings>;
    priority: number;
    sound: string;
    timeout_ms: number;
    suppress_transferred_completion: boolean;
};
type Agent = { slug: string; name?: string; display_name?: string };
type Provider = { key: string; label: string; ready: boolean };
const assignmentDefaults = (): Assignment => ({
    enabled: false,
    pre_call: [],
    in_call: [],
    post_call: [],
    destination_key: '',
    update_keys: ['handoff'],
});
const control =
    'w-full rounded-md border border-border bg-background text-foreground px-3 py-2 text-sm';

function writable(cfg: Config) {
    const result = structuredClone(cfg);
    delete result.token_configured;
    Object.values(result.recipients).forEach(recipient => delete recipient.key_configured);
    return result;
}

export default function PushoverPanel({ onSaved }: { onSaved: () => void }) {
    const [config, setConfig] = useState<Config | null>(null);
    const [agents, setAgents] = useState<Agent[]>([]);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [destinations, setDestinations] = useState<Record<string, { description?: string }>>({});
    const [token, setToken] = useState('');
    const [keys, setKeys] = useState<Record<string, string>>({});
    const [newRecipient, setNewRecipient] = useState('');
    const [status, setStatus] = useState('');
    const [busy, setBusy] = useState(false);
    const [preview, setPreview] = useState<{ title: string; message: string } | null>(null);
    const [dirty, setDirty] = useState(false);
    useEffect(() => {
        let cancelled = false;
        Promise.all([
            axios.get('/api/pushover'),
            axios.get('/api/agents'),
            axios.get('/api/config/providers/llm-options'),
            axios.get('/api/config'),
        ])
            .then(([settings, agentList, catalog, general]) => {
                if (cancelled) return;
                setConfig(settings.data);
                setAgents(agentList.data);
                setProviders(catalog.data.providers || []);
                setDestinations(general.data?.tools?.transfer?.destinations || {});
            })
            .catch(() => {
                if (!cancelled)
                    setStatus('Unable to load Pushover settings. Reload this panel to retry.');
            });
        return () => {
            cancelled = true;
        };
    }, []);
    useEffect(() => {
        const guard = (event: BeforeUnloadEvent) => {
            if (dirty || token || Object.values(keys).some(Boolean)) {
                event.preventDefault();
                event.returnValue = '';
            }
        };
        window.addEventListener('beforeunload', guard);
        return () => window.removeEventListener('beforeunload', guard);
    }, [dirty, token, keys]);
    const change = (next: Config) => {
        setConfig(next);
        setDirty(true);
        setPreview(null);
    };
    const updatePhase = (phase: Phase, patch: Partial<SummarySettings>) => {
        if (config)
            change({
                ...config,
                phases: { ...config.phases, [phase]: { ...config.phases[phase], ...patch } },
            });
    };
    const updateAgent = (slug: string, patch: Partial<Assignment>) => {
        if (config)
            change({
                ...config,
                agents: {
                    ...config.agents,
                    [slug]: { ...assignmentDefaults(), ...config.agents[slug], ...patch },
                },
            });
    };
    const save = async () => {
        if (!config) return;
        setBusy(true);
        setStatus('Saving…');
        try {
            await axios.put('/api/pushover', writable(config));
            if (token) {
                await axios.put('/api/pushover/credentials/application', { value: token });
                setToken('');
            }
            for (const [key, value] of Object.entries(keys)) {
                if (value && config.recipients[key]) {
                    await axios.put(
                        `/api/pushover/credentials/recipient?recipient=${encodeURIComponent(key)}`,
                        { value }
                    );
                    setKeys(previous => ({ ...previous, [key]: '' }));
                }
            }
            const result = await axios.get('/api/pushover');
            setConfig(result.data);
            setDirty(false);
            onSaved();
            setStatus(
                'Saved. Apply to new calls using Apply settings below. Active calls retain their captured configuration.'
            );
        } catch {
            setStatus(
                'Save did not complete. Check identifiers, summary provider readiness and key format, then retry. Earlier successful changes remain saved.'
            );
        } finally {
            setBusy(false);
        }
    };
    const recipientAction = async (key: string, action: 'validate' | 'test') => {
        setBusy(true);
        setStatus(action === 'test' ? 'Sending explicit test…' : 'Validating recipient…');
        try {
            const response = await axios.post(`/api/pushover/${action}`, { recipient: key });
            setStatus(
                `${action === 'test' ? 'Test' : 'Validation'}: ${response.data.delivery_status}${response.data.error_code ? ` (${response.data.error_code})` : ''}. Accepted means queued/valid, not displayed or read.`
            );
        } catch {
            setStatus('Unable to complete the requested action. No automatic retry will be made.');
        } finally {
            setBusy(false);
        }
    };
    if (!config) return <p role="status">{status || 'Loading Pushover settings…'}</p>;
    const agentSlugs = Array.from(
        new Set([...agents.map(agent => agent.slug), ...Object.keys(config.agents)])
    );
    return (
        <section className="space-y-6" aria-label="Native Pushover">
            <div>
                <h2 className="text-xl font-semibold">Pushover notifications</h2>
                <p className="text-sm text-muted-foreground">
                    Send arrival, briefing and completion notifications. Recipients and phases are
                    selected per agent. New agents start disabled.
                </p>
            </div>
            <fieldset disabled={busy} className="space-y-6 min-w-0">
                <FormSwitch
                    label="Enable Pushover"
                    checked={config.enabled}
                    onChange={e => change({ ...config, enabled: e.target.checked })}
                />
                <div className="space-y-3 rounded-lg border border-border p-4">
                    <FormInput
                        label="Application token"
                        type="password"
                        autoComplete="new-password"
                        value={token}
                        onChange={e => setToken(e.target.value)}
                        placeholder={
                            config.token_configured
                                ? 'Configured — enter a replacement'
                                : 'Enter application token'
                        }
                        tooltip="Stored server-side in an owner-only credential file. Blank preserves the saved token. Saving never sends a notification."
                    />
                    <p className="text-xs text-muted-foreground">
                        {config.token_configured
                            ? 'Application token configured'
                            : 'Application token missing'}
                    </p>
                    <button
                        type="button"
                        className="text-sm underline"
                        disabled={!config.token_configured || dirty || Boolean(token)}
                        onClick={async () => {
                            setBusy(true);
                            try {
                                const response = await axios.delete(
                                    '/api/pushover/credentials/application'
                                );
                                setConfig(response.data.config);
                                onSaved();
                                setStatus(
                                    'Application credential cleared. Apply settings to new calls.'
                                );
                            } catch {
                                setStatus('Unable to clear application credential.');
                            } finally {
                                setBusy(false);
                            }
                        }}
                    >
                        Clear saved application token
                    </button>
                </div>
                <div className="grid gap-4 md:grid-cols-3">
                    <FormSelect
                        label="Priority"
                        value={String(config.priority)}
                        options={[
                            { value: '-2', label: 'Lowest' },
                            { value: '-1', label: 'Low' },
                            { value: '0', label: 'Normal' },
                            { value: '1', label: 'High' },
                        ]}
                        onChange={e => change({ ...config, priority: Number(e.target.value) })}
                    />
                    <FormInput
                        label="Sound"
                        value={config.sound}
                        onChange={e => change({ ...config, sound: e.target.value })}
                        placeholder="User default"
                        tooltip="Leave blank to respect each user's sound. Enter a supported Pushover sound identifier or a custom sound belonging to your application account."
                    />
                    <FormInput
                        label="Delivery timeout (ms)"
                        type="number"
                        min={100}
                        max={10000}
                        value={config.timeout_ms}
                        onChange={e => change({ ...config, timeout_ms: Number(e.target.value) })}
                    />
                </div>
                <div className="space-y-4">
                    <h3 className="font-semibold">Named recipients</h3>
                    {Object.entries(config.recipients).map(([key, recipient]) => (
                        <div key={key} className="space-y-3 rounded-lg border border-border p-4">
                            <p className="font-mono text-sm">{key}</p>
                            <FormInput
                                label={`Recipient label (${key})`}
                                value={recipient.label}
                                onChange={e =>
                                    change({
                                        ...config,
                                        recipients: {
                                            ...config.recipients,
                                            [key]: { ...recipient, label: e.target.value },
                                        },
                                    })
                                }
                            />
                            <FormSwitch
                                label={`Enable recipient ${key}`}
                                checked={recipient.enabled}
                                onChange={e =>
                                    change({
                                        ...config,
                                        recipients: {
                                            ...config.recipients,
                                            [key]: { ...recipient, enabled: e.target.checked },
                                        },
                                    })
                                }
                            />
                            <FormInput
                                label={`User/group key (${key})`}
                                type="password"
                                autoComplete="new-password"
                                value={keys[key] || ''}
                                onChange={e => setKeys({ ...keys, [key]: e.target.value })}
                                placeholder={
                                    recipient.key_configured
                                        ? 'Configured — enter a replacement'
                                        : 'Enter user/group key'
                                }
                                tooltip="A single user or group key. Keys stay server-side; the voice model cannot choose or see them."
                            />
                            <div className="flex flex-wrap gap-4 text-sm">
                                <button
                                    type="button"
                                    disabled={
                                        dirty ||
                                        Boolean(token) ||
                                        Boolean(keys[key]) ||
                                        !recipient.enabled ||
                                        !recipient.key_configured ||
                                        !config.token_configured
                                    }
                                    onClick={() => void recipientAction(key, 'validate')}
                                    className="underline disabled:opacity-40"
                                >
                                    Validate {key}
                                </button>
                                <button
                                    type="button"
                                    disabled={
                                        dirty ||
                                        Boolean(token) ||
                                        Boolean(keys[key]) ||
                                        !recipient.enabled ||
                                        !recipient.key_configured ||
                                        !config.token_configured
                                    }
                                    onClick={() => void recipientAction(key, 'test')}
                                    className="underline disabled:opacity-40"
                                >
                                    Send test to {key}
                                </button>
                                <button
                                    type="button"
                                    onClick={() => {
                                        const next = { ...config.recipients };
                                        delete next[key];
                                        change({ ...config, recipients: next });
                                        setKeys(previous => {
                                            const result = { ...previous };
                                            delete result[key];
                                            return result;
                                        });
                                    }}
                                    className="text-destructive underline"
                                >
                                    Remove {key}
                                </button>
                            </div>
                        </div>
                    ))}
                    <div className="flex gap-3 items-end">
                        <FormInput
                            label="New recipient identifier"
                            value={newRecipient}
                            onChange={e => setNewRecipient(e.target.value)}
                            placeholder="support_team"
                        />
                        <button
                            type="button"
                            className="rounded border border-border px-3 py-2"
                            disabled={
                                !/^[a-z][a-z0-9_]{0,47}$/.test(newRecipient) ||
                                Boolean(config.recipients[newRecipient])
                            }
                            onClick={() => {
                                change({
                                    ...config,
                                    recipients: {
                                        ...config.recipients,
                                        [newRecipient]: { label: newRecipient, enabled: true },
                                    },
                                });
                                setNewRecipient('');
                            }}
                        >
                            Add recipient
                        </button>
                    </div>
                </div>
                <div className="space-y-4">
                    <h3 className="font-semibold">Agent recipients and phases</h3>
                    <p className="text-sm text-muted-foreground">
                        Arrival runs after selected enrichment. Briefing exposes pushover_briefing
                        to the agent; instruct it to invoke before transfer. Completion uses the
                        final AVA outcome.
                    </p>
                    {agentSlugs.map(slug => {
                        const assignment = { ...assignmentDefaults(), ...config.agents[slug] };
                        return (
                            <div
                                key={slug}
                                className="rounded-lg border border-border p-4 space-y-3"
                            >
                                <FormSwitch
                                    label={`Notifications for ${agents.find(agent => agent.slug === slug)?.display_name || slug}`}
                                    checked={assignment.enabled}
                                    onChange={e => updateAgent(slug, { enabled: e.target.checked })}
                                />
                                <div className="grid md:grid-cols-3 gap-4">
                                    {phases.map(phase => (
                                        <div key={phase}>
                                            <p className="font-medium text-sm mb-2">
                                                {phaseNames[phase]}
                                            </p>
                                            {Object.entries(config.recipients).map(
                                                ([key, recipient]) => (
                                                    <label
                                                        key={key}
                                                        className="flex gap-2 text-sm mb-2"
                                                    >
                                                        <input
                                                            type="checkbox"
                                                            aria-label={`${slug} ${phaseNames[phase]} ${key}`}
                                                            checked={assignment[phase].includes(
                                                                key
                                                            )}
                                                            onChange={e =>
                                                                updateAgent(slug, {
                                                                    [phase]: e.target.checked
                                                                        ? [
                                                                              ...assignment[phase],
                                                                              key,
                                                                          ]
                                                                        : assignment[phase].filter(
                                                                              value => value !== key
                                                                          ),
                                                                })
                                                            }
                                                        />
                                                        {recipient.label || key}
                                                        {!recipient.enabled && ' (disabled)'}
                                                    </label>
                                                )
                                            )}
                                            {assignment[phase]
                                                .filter(key => !config.recipients[key])
                                                .map(key => (
                                                    <p
                                                        key={key}
                                                        className="text-xs text-destructive"
                                                    >
                                                        Missing recipient: {key}. This phase fails
                                                        closed until reassigned.
                                                        <button
                                                            type="button"
                                                            className="underline ml-2"
                                                            onClick={() =>
                                                                updateAgent(slug, {
                                                                    [phase]: assignment[
                                                                        phase
                                                                    ].filter(
                                                                        value => value !== key
                                                                    ),
                                                                })
                                                            }
                                                        >
                                                            Remove assignment
                                                        </button>
                                                    </p>
                                                ))}
                                        </div>
                                    ))}
                                </div>
                                <FormSelect
                                    label={`Intended transfer destination (${slug})`}
                                    value={assignment.destination_key}
                                    options={[
                                        {
                                            value: '',
                                            label: 'Unspecified — never suppress completion',
                                        },
                                        ...Object.entries(destinations).map(([key, value]) => ({
                                            value: key,
                                            label: value.description || key,
                                        })),
                                    ]}
                                    onChange={e =>
                                        updateAgent(slug, { destination_key: e.target.value })
                                    }
                                    tooltip="Operator-owned destination for a pre-transfer briefing. Completion is suppressed only after an actual matching transfer and an accepted briefing to that recipient."
                                />
                                <FormInput
                                    label={`Allowed update keys (${slug})`}
                                    value={assignment.update_keys.join(',')}
                                    onChange={e =>
                                        updateAgent(slug, {
                                            update_keys: e.target.value
                                                .split(',')
                                                .map(value => value.trim()),
                                        })
                                    }
                                    tooltip="Comma-separated event identifiers, for example handoff,callback. Retries for the same caller turn/event are deduplicated; a later caller request or transfer attempt is a distinct update."
                                />
                            </div>
                        );
                    })}
                </div>
                <FormSwitch
                    label="Suppress matching transferred-call completion"
                    checked={config.suppress_transferred_completion}
                    onChange={e =>
                        change({ ...config, suppress_transferred_completion: e.target.checked })
                    }
                    description="Only after a verified transfer with the matching accepted briefing. Failed or cancelled transfers still receive completion."
                />
                {phases.map(phase => (
                    <div key={phase} className="rounded-lg border border-border p-4 space-y-4">
                        <h3 className="font-semibold">{phaseNames[phase]} message</h3>
                        <FormInput
                            label={`${phaseNames[phase]} title`}
                            value={config.phases[phase]?.title || ''}
                            onChange={e => updatePhase(phase, { title: e.target.value })}
                            placeholder="Use recommended default"
                        />
                        <div>
                            <FormLabel tooltip="Available: {agent}, {stage}, {name}, {identity_source}, {company}, {crm_status}, {caller_number}, {callback_number}, {destination}, {call_id}, {outcome}, {language}, {meeting}, {summary}, {summary_status}, {update_key}. Missing data is unknown. Templates are plain text.">
                                {phaseNames[phase]} body
                            </FormLabel>
                            <textarea
                                aria-label={`${phaseNames[phase]} body`}
                                className={`${control} min-h-28`}
                                value={config.phases[phase]?.body || ''}
                                onChange={e => updatePhase(phase, { body: e.target.value })}
                                placeholder="Use recommended default"
                            />
                        </div>
                        {phase !== 'pre_call' && (
                            <>
                                <FormSwitch
                                    label={`Generate ${phaseNames[phase].toLowerCase()} summary`}
                                    checked={config.phases[phase]?.generate_summary || false}
                                    onChange={e =>
                                        updatePhase(phase, { generate_summary: e.target.checked })
                                    }
                                />
                                {config.phases[phase]?.generate_summary && (
                                    <div className="space-y-3">
                                        <FormSelect
                                            label={`${phaseNames[phase]} summary provider`}
                                            value={config.phases[phase].summary_provider || ''}
                                            options={[
                                                { value: '', label: 'Select a configured LLM' },
                                                ...providers.map(provider => ({
                                                    value: provider.key,
                                                    label: `${provider.label}${provider.ready ? '' : ' (not ready)'}`,
                                                    disabled: !provider.ready,
                                                })),
                                            ]}
                                            onChange={e =>
                                                updatePhase(phase, {
                                                    summary_provider: e.target.value,
                                                })
                                            }
                                        />
                                        <div>
                                            <FormLabel>
                                                Summary prompt ({phaseNames[phase]})
                                            </FormLabel>
                                            <textarea
                                                aria-label={`Summary prompt (${phaseNames[phase]})`}
                                                className={control}
                                                value={config.phases[phase].summary_prompt}
                                                onChange={e =>
                                                    updatePhase(phase, {
                                                        summary_prompt: e.target.value,
                                                    })
                                                }
                                            />
                                        </div>
                                        <div className="grid md:grid-cols-3 gap-3">
                                            <FormInput
                                                label={`Maximum words (${phaseNames[phase]})`}
                                                type="number"
                                                min={10}
                                                max={1000}
                                                value={config.phases[phase].summary_max_words}
                                                onChange={e =>
                                                    updatePhase(phase, {
                                                        summary_max_words: Number(e.target.value),
                                                    })
                                                }
                                            />
                                            <FormInput
                                                label={`Summary timeout (${phaseNames[phase]})`}
                                                type="number"
                                                min={1000}
                                                max={120000}
                                                value={config.phases[phase].summary_timeout_ms}
                                                onChange={e =>
                                                    updatePhase(phase, {
                                                        summary_timeout_ms: Number(e.target.value),
                                                    })
                                                }
                                            />
                                            <FormSelect
                                                label={`Summary failure (${phaseNames[phase]})`}
                                                value={config.phases[phase].summary_failure_policy}
                                                options={[
                                                    {
                                                        value: 'continue',
                                                        label: 'Deliver with summary unavailable',
                                                    },
                                                    { value: 'abort', label: 'Skip notification' },
                                                ]}
                                                onChange={e =>
                                                    updatePhase(phase, {
                                                        summary_failure_policy: e.target.value as
                                                            | 'continue'
                                                            | 'abort',
                                                    })
                                                }
                                            />
                                        </div>
                                    </div>
                                )}
                            </>
                        )}
                        <button
                            type="button"
                            className="underline text-sm"
                            onClick={async () => {
                                try {
                                    const response = await axios.post('/api/pushover/preview', {
                                        phase,
                                        config: writable(config),
                                    });
                                    setPreview(response.data);
                                } catch {
                                    setStatus(
                                        'Preview failed. Check template variables and summary settings.'
                                    );
                                }
                            }}
                        >
                            Preview {phaseNames[phase].toLowerCase()}
                        </button>
                    </div>
                ))}
                {preview && (
                    <div className="rounded border border-border p-4">
                        <p className="font-medium">{preview.title}</p>
                        <pre className="whitespace-pre-wrap text-sm mt-2">{preview.message}</pre>
                        <p className="text-xs text-muted-foreground mt-2">
                            Synthetic preview only. No notification or model request was sent.
                        </p>
                    </div>
                )}
                <div className="flex gap-4">
                    <button
                        type="button"
                        onClick={() => void save()}
                        className="rounded bg-primary text-primary-foreground px-4 py-2"
                    >
                        Save Pushover settings
                    </button>
                    <button
                        type="button"
                        disabled={dirty || Boolean(token) || Object.values(keys).some(Boolean)}
                        className="rounded border border-border px-4 py-2 disabled:opacity-40"
                        onClick={async () => {
                            setBusy(true);
                            try {
                                await axios.post('/api/system/containers/ai_engine/reload');
                                setStatus(
                                    'Applied to new calls. Active calls keep their captured configuration.'
                                );
                                onSaved();
                            } catch {
                                setStatus(
                                    'Apply failed. Saved settings remain available; inspect engine health and retry.'
                                );
                            } finally {
                                setBusy(false);
                            }
                        }}
                    >
                        Apply settings
                    </button>
                </div>
            </fieldset>
            <p role="status" className="text-sm">
                {status}
            </p>
            <p className="text-xs text-muted-foreground">
                Best-effort delivery with bounded queues and two connections. No automatic retry
                after uncertain delivery. Duplicate protection lasts for the current call/process;
                restart replay and delivery/read receipts are unavailable.
            </p>
        </section>
    );
}
