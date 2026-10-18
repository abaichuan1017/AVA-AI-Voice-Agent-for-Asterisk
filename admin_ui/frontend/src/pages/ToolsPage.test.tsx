// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import yaml from 'js-yaml';
import ToolsPage from './ToolsPage';

const mocks = vi.hoisted(() => ({
    load: vi.fn(), confirm: vi.fn(), refetch: vi.fn(),
    state: { restartRequired: false, applyRequired: false, recommendedApplyMethod: 'none', stateStale: false },
}));
vi.mock('../auth/AuthContext', () => ({ useAuth: () => ({ token: 'test-token' }) }));
vi.mock('../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: mocks.confirm }) }));
vi.mock('../hooks/useRestartRequired', () => ({ useRestartRequired: () => ({ ...mocks.state, refetch: mocks.refetch }) }));
vi.mock('../utils/configCache', () => ({ getCachedConfig: () => null, loadConfigYaml: mocks.load }));
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn(), isCancel: () => false } }));
vi.mock('@monaco-editor/react', () => ({
    default: ({ value, onChange }: { value: string; onChange: (value: string) => void }) =>
        <textarea aria-label="Template source" value={value} onChange={event => onChange(event.target.value)} />,
}));

const fixture = () => ({
    default_provider: 'google_live',
    providers: { google_live: { enabled: true, custom_option: 'preserve-provider' } },
    on_provider_failure: 'hangup', provider_failure_prompt: 'sorry-youre-having-problems',
    tools: {
        ai_identity: { name: 'Test agent', number: '6789' },
        transfer: { enabled: true, technology: 'PJSIP', deferred_audio_drain_timeout_sec: 19, handoff: { enabled: true, message: 'Connecting you.', timeout_sec: 7 }, destinations: { reception: { type: 'extension', target: '100', dialplan_context: 'custom-context', handoff: { mode: 'disabled' } } } },
        attended_transfer: { enabled: false, custom_screening: 'preserve' },
        cancel_transfer: { enabled: true, allow_during_ring: false },
        check_extension_status: { restrict_to_configured_extensions: true },
        hangup_call: { enabled: true, farewell_message: 'Goodbye.', policy: { mode: 'strict', markers: { end_call: ['custom marker'] } } },
        leave_voicemail: { enabled: true, mailboxes: { default: { name: 'Reception', extension: '100' } }, default_mailbox: 'default' },
        send_email_summary: { enabled: true, admin_email: 'admin@example.com', html_template: '<p>Existing custom template</p>' },
        request_transcript: { enabled: true, confirm_email: true },
        google_calendar: { enabled: true, calendars: {}, custom_availability: { working_days: [1, 2] } },
        microsoft_calendar: { enabled: true, accounts: {}, allow_caller_invitations: false, max_booking_duration_minutes: 240 },
        private_custom: { kind: 'custom', preserve: { nested: [0, false, 'x'] } },
        before: { kind: 'generic_http_lookup', phase: 'pre_call', enabled: true, url: 'https://example.com/before', method: 'GET', headers: { Authorization: 'Bearer ${CRM_KEY}' }, output_variables: { customer: 'name' } },
        after: { kind: 'generic_webhook', phase: 'post_call', enabled: true, is_global: true, url: 'https://example.com/after', method: 'POST', headers: {}, payload_template: '{"summary":{summary_json}}' },
    },
    in_call_tools: { lookup: { kind: 'in_call_http_lookup', phase: 'in_call', enabled: true, url: 'https://example.com/lookup', method: 'GET', headers: {}, description: 'Check stock', parameters: [], custom_field: 'retain' } },
    contexts: { sales: { tools: ['hangup_call'], pre_call_tools: ['before'], disable_global_post_call_tools: ['after'], tool_overrides: { leave_voicemail: { mailbox: 'default' } } } },
    mcp: { servers: { example: { command: 'existing-server' } } },
});

beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    Object.assign(mocks.state, { restartRequired: false, applyRequired: false, recommendedApplyMethod: 'none', stateStale: false });
    mocks.load.mockResolvedValue({ config: fixture(), yamlError: null });
    mocks.refetch.mockResolvedValue(undefined);
    mocks.confirm.mockResolvedValue(false);
    vi.mocked(axios.get).mockResolvedValue({ data: { tools: [], providers: [] } });
    vi.mocked(axios.post).mockImplementation(async url => ({ data: url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { tool_generation: 9 } }));
});

async function open(name: string) {
    await screen.findByRole('heading', { name: 'Tools' });
    fireEvent.click(screen.getByRole('button', { name: `Configure ${name}` }));
}
async function editFarewell() {
    await open('Hangup');
    fireEvent.change(screen.getByLabelText('Default Farewell Message'), { target: { value: 'See you soon.' } });
}
function postedConfig() {
    const call = vi.mocked(axios.post).mock.calls.find(([url]) => url === '/api/config/yaml');
    return yaml.load((call?.[1] as { content: string }).content) as ReturnType<typeof fixture>;
}

describe('Tools list and dedicated editors', () => {
    it('starts with a compact list and preserves the full config through editing, navigation and Save & Apply', async () => {
        render(<ToolsPage />);
        await screen.findByRole('heading', { name: 'Tools' });
        expect(screen.queryByRole('textbox', { name: 'Default Farewell Message' })).not.toBeInTheDocument();
        expect(screen.queryByRole('button', { name: 'Save & Apply' })).not.toBeInTheDocument();
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        fireEvent.click(screen.getByRole('button', { name: 'Pre-Call' }));
        fireEvent.click(screen.getByRole('button', { name: 'In-Call' }));
        await open('Hangup');
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const expected = fixture();
        expected.tools.hangup_call.farewell_message = 'See you soon.';
        expect(postedConfig()).toEqual(expected);
        expect(vi.mocked(axios.post).mock.calls.map(([url]) => url)).toEqual(['/api/config/yaml', '/api/system/containers/ai_engine/reload']);
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
    });

    it('keeps every built-in editor reachable and makes catalog read-only', async () => {
        render(<ToolsPage />);
        for (const [name, control] of [
            ['Transfer & live agents', 'Transfer Tool'], ['Hangup', 'Hangup Call'], ['Voicemail', 'Leave Voicemail'],
            ['Email summary', 'Send Email Summary'], ['Transcript requests', 'Request Transcript'],
            ['Google Calendar', 'Google Calendar'], ['Microsoft Calendar', 'Microsoft Calendar'],
        ]) {
            await open(name);
            expect(screen.getByRole('checkbox', { name: control })).toBeVisible();
            expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
            fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        }
        fireEvent.click(screen.getByRole('button', { name: 'Tool defaults' }));
        expect(screen.getByRole('textbox', { name: 'Agent Name' })).toBeVisible();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        fireEvent.click(screen.getByRole('button', { name: 'Catalog' }));
        expect(screen.getByRole('heading', { name: 'Tool Catalog (Read-only)' })).toBeVisible();
        expect(screen.queryByRole('button', { name: 'Save & Apply' })).not.toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('searches configured tools and does not change hidden settings when opening advanced controls', async () => {
        render(<ToolsPage />);
        await screen.findByRole('heading', { name: 'Tools' });
        fireEvent.change(screen.getByRole('textbox', { name: 'Search tools' }), { target: { value: 'calendar' } });
        expect(screen.getByRole('button', { name: 'Configure Microsoft Calendar' })).toBeVisible();
        expect(screen.queryByRole('button', { name: 'Configure Hangup' })).not.toBeInTheDocument();
        fireEvent.change(screen.getByRole('textbox', { name: 'Search tools' }), { target: { value: '' } });
        await open('Transfer & live agents');
        const disclosure = screen.getByText('Advanced transfer settings — routing and timing').closest('details')!;
        expect(disclosure).not.toHaveAttribute('open');
        fireEvent.click(within(disclosure).getByText('Advanced transfer settings — routing and timing'));
        expect(screen.getByLabelText('Deferred Audio Drain Timeout (seconds)')).toHaveValue(19);
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('keeps HTTP editing inline and stages changes without publishing until Save & Apply', async () => {
        render(<ToolsPage />);
        await screen.findByRole('button', { name: 'Edit lookup' });
        fireEvent.click(screen.getByRole('button', { name: 'Edit lookup' }));
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(screen.getByRole('heading', { name: 'Edit lookup' })).toBeVisible();
        expect(screen.queryByRole('button', { name: 'Save & Apply' })).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.com/new-lookup' } });
        fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
        expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(await screen.findByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const saved = postedConfig();
        expect(saved.in_call_tools.lookup.url).toBe('https://example.com/new-lookup');
        expect(saved.in_call_tools.lookup.custom_field).toBe('retain');
        expect(saved.tools).toEqual(fixture().tools);
        expect(saved.contexts).toEqual(fixture().contexts);
    });

    it('cancels an HTTP draft without modifying the page draft or issuing a request', async () => {
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Edit lookup' }));
        fireEvent.change(screen.getByLabelText('URL'), { target: { value: 'https://example.com/discard' } });
        fireEvent.click(screen.getByRole('button', { name: 'Cancel and return to tools' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Edit lookup' }));
        expect(screen.getByLabelText('URL')).toHaveValue('https://example.com/lookup');
        expect(axios.post).not.toHaveBeenCalled();
    });

    it('commits a blur-based transfer value before saving with the mouse', async () => {
        const user = userEvent.setup();
        render(<ToolsPage />);
        await open('Transfer & live agents');
        await user.click(screen.getByRole('button', { name: 'Show State Value Mapping' }));
        const free = screen.getByLabelText('Free');
        await user.clear(free);
        await user.type(free, 'NOT_INUSE CUSTOM_READY');
        await user.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect((postedConfig().tools.check_extension_status as unknown as { state_mapping: { free: string[] } }).state_mapping.free).toEqual(['NOT_INUSE', 'CUSTOM_READY']);
    });

    it('keeps a pending Microsoft connection alive across navigation without overwriting other tool edits', async () => {
        const config = fixture();
        config.tools.microsoft_calendar.accounts = { default: { tenant_id: 'tenant', client_id: 'client', timezone: 'UTC' } };
        mocks.load.mockResolvedValue({ config, yamlError: null });
        let finish!: (value: unknown) => void;
        const pending = new Promise(resolve => { finish = resolve; });
        vi.mocked(axios.post).mockImplementation(async url => ({ data: url.toString().endsWith('/device/start')
            ? { flow_id: 'test-flow', user_code: 'EXAMPLE', verification_uri: 'https://microsoft.com/devicelogin' }
            : url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { tool_generation: 9 } }));
        vi.mocked(axios.get).mockImplementation(async url => url.toString().includes('/device/status/') ? pending : { data: { tools: [], providers: [] } });
        render(<ToolsPage />);
        await open('Microsoft Calendar');
        fireEvent.click(screen.getByRole('button', { name: 'Connect' }));
        await screen.findByText('Authorize Microsoft Calendar');
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await editFarewell();
        await act(async () => finish({ data: { status: 'success', result: { token_cache_path: '/app/project/secrets/test-cache.json', user_principal_name: 'test@example.com', calendars: [{ id: 'calendar', name: 'Appointments', is_default_calendar: true }] } } }));
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await open('Microsoft Calendar');
        expect(screen.getByLabelText('Signed-in user')).toHaveValue('test@example.com');
        expect(vi.mocked(axios.post).mock.calls.filter(([url]) => url.toString().endsWith('/device/start'))).toHaveLength(1);
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(postedConfig().tools.hangup_call.farewell_message).toBe('See you soon.');
    });

    it('merges Google verification autofill into the latest draft after changing editors', async () => {
        const config = fixture();
        config.tools.google_calendar.calendars = { office: { credentials_path: '/app/project/secrets/test.json', calendar_id: 'office@example.com', timezone: '' } };
        mocks.load.mockResolvedValue({ config, yamlError: null });
        let finish!: (value: unknown) => void;
        const pending = new Promise(resolve => { finish = resolve; });
        vi.mocked(axios.post).mockImplementation(async url => url.toString().endsWith('/office/verify') ? pending
            : { data: url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { tool_generation: 9 } });
        render(<ToolsPage />);
        await open('Google Calendar');
        fireEvent.click(screen.getByRole('button', { name: /Verify access/ }));
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await editFarewell();
        await act(async () => finish({ data: { calendar_actual_timezone: 'UTC', calendar_summary: 'Office' } }));
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(postedConfig().tools.google_calendar.calendars).toEqual({ office: { credentials_path: '/app/project/secrets/test.json', calendar_id: 'office@example.com', timezone: 'UTC' } });
        expect(postedConfig().tools.hangup_call.farewell_message).toBe('See you soon.');
    });
});

describe('Tools persistence and apply outcomes', () => {
    it('preserves the immediate email-template save path and other staged tool edits', async () => {
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Back to tools' }));
        await open('Email summary');
        fireEvent.click(screen.getByRole('button', { name: 'Show Advanced Email Format' }));
        fireEvent.click(screen.getByRole('button', { name: 'Edit / Preview' }));
        const dialog = screen.getByRole('dialog');
        fireEvent.change(within(dialog).getByLabelText('Template source'), { target: { value: '<p>Updated template</p>' } });
        fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }));
        await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(postedConfig().tools.send_email_summary.html_template).toBe('<p>Updated template</p>');
        expect(postedConfig().tools.hangup_call.farewell_message).toBe('See you soon.');
        expect(mocks.refetch).toHaveBeenCalled();
    });

    it.each(['Pre-Call', 'Post-Call'])('preserves the other phases when updating a %s HTTP tool', async phase => {
        render(<ToolsPage />);
        await screen.findByRole('heading', { name: 'Tools' });
        fireEvent.click(screen.getByRole('button', { name: phase }));
        const key = phase === 'Pre-Call' ? 'before' : 'after';
        fireEvent.click(screen.getByRole('button', { name: `Edit ${key}` }));
        fireEvent.change(screen.getByLabelText('URL'), { target: { value: `https://example.com/new-${key}` } });
        fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
        fireEvent.click(await screen.findByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(/Applied as tool generation 9/);
        const expected = fixture();
        expected.tools[key].url = `https://example.com/new-${key}`;
        expect(postedConfig()).toEqual(expected);
    });

    it('retains unsaved edits and never reloads after a save failure', async () => {
        vi.mocked(axios.post).mockRejectedValueOnce(new Error('Save failed'));
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Tools were not saved.');
        expect(screen.getByLabelText('Default Farewell Message')).toHaveValue('See you soon.');
        expect(screen.getByRole('button', { name: 'Save & Apply' })).toBeEnabled();
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('distinguishes a persisted draft from a failed apply and refreshes pending status', async () => {
        vi.mocked(axios.post).mockImplementation(async url => {
            if (url === '/api/config/yaml') return { data: { recommended_apply_method: 'hot_reload' } };
            throw new Error('Engine unavailable');
        });
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Saved changes could not be applied; the previous tool generation remains active.');
        expect(mocks.refetch).toHaveBeenCalled();
        expect(screen.queryByText('Unsaved changes')).not.toBeInTheDocument();
    });

    it('does not report full success after a partial hot reload', async () => {
        vi.mocked(axios.post).mockImplementation(async url => ({ data: url === '/api/config/yaml' ? { recommended_apply_method: 'hot_reload' } : { restart_required: true } }));
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText('Saved and partially applied. Restart the AI Engine to finish applying changes.');
        expect(screen.queryByText(/Applied as tool generation/)).not.toBeInTheDocument();
    });

    it.each(['none', 'restart'])('honors the backend %s apply plan without automatically restarting', async method => {
        vi.mocked(axios.post).mockResolvedValue({ data: { recommended_apply_method: method } });
        render(<ToolsPage />);
        await editFarewell();
        fireEvent.click(screen.getByRole('button', { name: 'Save & Apply' }));
        await screen.findByText(method === 'none' ? 'Saved. No apply action is required.' : 'Saved. A restart is required for these changes.');
        expect(axios.post).toHaveBeenCalledTimes(1);
    });

    it('keeps stale pending state visible and respects an active-call restart warning', async () => {
        Object.assign(mocks.state, { restartRequired: true, recommendedApplyMethod: 'restart', stateStale: true });
        vi.mocked(axios.post).mockResolvedValue({ data: { status: 'warning', message: 'One active call.' } });
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Restart AI Engine' }));
        await waitFor(() => expect(mocks.confirm).toHaveBeenCalledWith(expect.objectContaining({ title: 'Force Restart?', variant: 'destructive' })));
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenCalledWith('/api/system/containers/ai_engine/restart?force=false', {}, expect.anything());
        expect(screen.getByText(/Status refresh failed; showing the last known action/)).toBeVisible();
    });

    it('can retry applying saved changes without saving the config again', async () => {
        Object.assign(mocks.state, { applyRequired: true, recommendedApplyMethod: 'hot_reload' });
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Apply Changes' }));
        await screen.findByText(/Applied as tool generation 9/);
        expect(axios.post).toHaveBeenCalledTimes(1);
        expect(axios.post).toHaveBeenCalledWith('/api/system/containers/ai_engine/reload', {}, expect.anything());
    });

    it('blocks editing after a load failure and supports retry', async () => {
        mocks.load.mockRejectedValueOnce(new Error('Network failure'));
        render(<ToolsPage />);
        fireEvent.click(await screen.findByRole('button', { name: 'Retry loading tools' }));
        await screen.findByRole('heading', { name: 'Tools' });
        expect(mocks.load).toHaveBeenLastCalledWith(true);
        expect(axios.post).not.toHaveBeenCalled();
    });
});
