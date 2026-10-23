// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import PushoverPanel from './PushoverPanel';
vi.mock('axios', () => ({
    default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() },
}));
const settings = () => ({
    enabled: false,
    token_configured: true,
    recipients: { staff: { label: 'Staff', enabled: true, key_configured: true } },
    agents: {},
    phases: Object.fromEntries(
        ['pre_call', 'in_call', 'post_call'].map(phase => [
            phase,
            {
                title: '',
                body: '',
                generate_summary: false,
                summary_prompt: 'Summarize in {max_words} words.',
                summary_max_words: 100,
                summary_timeout_ms: 15000,
                summary_failure_policy: 'continue',
            },
        ])
    ),
    priority: 0,
    sound: '',
    timeout_ms: 5000,
    suppress_transferred_completion: true,
});
beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockImplementation(async url => ({
        data:
            url === '/api/pushover'
                ? settings()
                : url === '/api/agents'
                  ? [{ slug: 'sales', display_name: 'Sales' }]
                  : url === '/api/config/providers/llm-options'
                    ? { providers: [{ key: 'mock_llm', label: 'Mock', ready: true }] }
                    : {
                          tools: {
                              transfer: { destinations: { support: { description: 'Support' } } },
                          },
                      },
    }));
    vi.mocked(axios.put).mockResolvedValue({ data: { config: settings() } });
    vi.mocked(axios.post).mockResolvedValue({
        data: { delivery_status: 'accepted', title: 'Synthetic preview', message: 'Preview only' },
    });
});
describe('native Pushover panel', () => {
    it('masks saved credentials and sends only on an explicit test click', async () => {
        render(<PushoverPanel onSaved={vi.fn()} />);
        await screen.findByRole('heading', { name: 'Pushover notifications' });
        expect(screen.getByLabelText('Application token')).toHaveValue('');
        expect(screen.getByLabelText('User/group key (staff)')).toHaveValue('');
        expect(axios.post).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: 'Validate staff' }));
        await waitFor(() =>
            expect(axios.post).toHaveBeenCalledWith('/api/pushover/validate', {
                recipient: 'staff',
            })
        );
        await screen.findByText(/Validation: accepted/);
        fireEvent.click(screen.getByRole('button', { name: 'Send test to staff' }));
        await waitFor(() =>
            expect(axios.post).toHaveBeenCalledWith('/api/pushover/test', { recipient: 'staff' })
        );
    });
    it('persists per-agent phase selections and keeps secrets outside the settings document', async () => {
        const saved = vi.fn();
        render(<PushoverPanel onSaved={saved} />);
        await screen.findByRole('heading', { name: 'Pushover notifications' });
        fireEvent.click(screen.getByLabelText('Notifications for Sales'));
        fireEvent.click(screen.getByLabelText('sales Briefing staff'));
        fireEvent.change(screen.getByLabelText('Application token'), {
            target: { value: 'a'.repeat(30) },
        });
        expect(screen.getByRole('button', { name: 'Send test to staff' })).toBeDisabled();
        fireEvent.click(screen.getByRole('button', { name: 'Save Pushover settings' }));
        await waitFor(() => expect(saved).toHaveBeenCalled());
        const calls = vi.mocked(axios.put).mock.calls;
        expect(calls[0][0]).toBe('/api/pushover');
        expect(calls[0][1]).toMatchObject({
            agents: { sales: { enabled: true, in_call: ['staff'] } },
        });
        expect(JSON.stringify(calls[0][1])).not.toContain('a'.repeat(30));
        expect(JSON.stringify(calls[0][1])).not.toContain('token_configured');
        expect(calls[1]).toEqual([
            '/api/pushover/credentials/application',
            { value: 'a'.repeat(30) },
        ]);
        expect(screen.getByLabelText('Application token')).toHaveValue('');
        expect(axios.post).not.toHaveBeenCalled();
    });
    it('previews synthetic data and allows named recipient removal', async () => {
        render(<PushoverPanel onSaved={vi.fn()} />);
        await screen.findByRole('heading', { name: 'Pushover notifications' });
        fireEvent.click(screen.getByRole('button', { name: 'Preview briefing' }));
        await screen.findByText('Synthetic preview');
        expect(vi.mocked(axios.post).mock.calls.map(call => call[0])).toEqual([
            '/api/pushover/preview',
        ]);
        fireEvent.click(screen.getByRole('button', { name: 'Remove staff' }));
        expect(screen.queryByLabelText('User/group key (staff)')).not.toBeInTheDocument();
        fireEvent.change(screen.getByLabelText('New recipient identifier'), {
            target: { value: 'operations' },
        });
        fireEvent.click(screen.getByRole('button', { name: 'Add recipient' }));
        expect(screen.getByLabelText('User/group key (operations)')).toBeInTheDocument();
    });
});
