export interface PushoverDraft {
    phase?: string;
    description?: string;
    recipient_label?: string;
    token_file?: string;
    key_file?: string;
    title?: string;
    body?: string;
    priority?: number;
    sound?: string;
    timeout_ms?: number;
    destination_key?: string;
    suppress_after_tool?: string;
    [key: string]: unknown;
}

export const pushoverFields = [
    'kind',
    'phase',
    'enabled',
    'is_global',
    'description',
    'token_file',
    'key_file',
    'recipient_label',
    'title',
    'body',
    'priority',
    'sound',
    'timeout_ms',
    'generate_summary',
    'summary_provider',
    'summary_prompt',
    'summary_max_words',
    'summary_timeout_ms',
    'summary_failure_policy',
    'destination_key',
    'suppress_after_tool',
];
export function pushoverDocument(value: PushoverDraft) {
    return Object.fromEntries(
        pushoverFields
            .filter(key => value[key] !== undefined && value[key] !== '')
            .map(key => [key, value[key]])
    );
}
