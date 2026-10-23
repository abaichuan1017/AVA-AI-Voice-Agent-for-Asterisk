// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import HTTPToolForm from './HTTPToolForm';
vi.mock('../../auth/AuthContext', () => ({ useAuth: () => ({ token: 'synthetic' }) }));
vi.mock('../../hooks/useConfirmDialog', () => ({ useConfirmDialog: () => ({ confirm: vi.fn() }) }));
vi.mock('axios', () => ({ default: { get: vi.fn(), post: vi.fn() } }));
beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(axios.get).mockResolvedValue({
        data: {
            providers: [
                {
                    key: 'test_llm',
                    label: 'Synthetic provider',
                    ready: true,
                    credential_configured: true,
                },
            ],
        },
    });
    vi.mocked(axios.post).mockImplementation(async url => ({
        data:
            url === '/api/pushover/credentials'
                ? { token_file: '/managed/application', key_file: '/managed/recipient' }
                : { title: 'Synthetic preview', message: 'Synthetic message', preview_only: true },
    }));
});
describe('Pushover in the existing tool editor', () => {
    it.each(['pre_call', 'in_call', 'post_call'] as const)(
        'stages a named %s tool with masked secret references',
        async phase => {
            const changed = vi.fn();
            render(
                <HTTPToolForm phase={phase} config={{}} onChange={changed} presentation="page" />
            );
            fireEvent.click(screen.getByRole('button', { name: 'Add Tool' }));
            fireEvent.change(screen.getByLabelText('Tool type'), { target: { value: 'pushover' } });
            expect(screen.queryByLabelText('URL')).not.toBeInTheDocument();
            fireEvent.change(screen.getByLabelText('Tool Name'), {
                target: { value: 'notify_sales' },
            });
            fireEvent.change(screen.getByLabelText('Recipient name'), {
                target: { value: 'Sales' },
            });
            fireEvent.change(screen.getByLabelText('Application token'), {
                target: { value: 'a'.repeat(30) },
            });
            fireEvent.change(screen.getByLabelText('User/group key'), {
                target: { value: 'b'.repeat(30) },
            });
            if (phase === 'in_call')
                fireEvent.change(screen.getByLabelText('Tool description / when to use'), {
                    target: { value: 'Use before a Sales handoff' },
                });
            expect(axios.post).not.toHaveBeenCalled();
            fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
            await waitFor(() => expect(changed).toHaveBeenCalledOnce());
            const doc = changed.mock.calls[0][0].notify_sales;
            expect(doc).toMatchObject({
                kind: 'pushover',
                phase,
                recipient_label: 'Sales',
                token_file: '/managed/application',
                key_file: '/managed/recipient',
            });
            expect(doc).not.toHaveProperty('url');
            expect(doc).not.toHaveProperty('parameters');
            expect(JSON.stringify(doc)).not.toContain('a'.repeat(30));
            expect(JSON.stringify(doc)).not.toContain('b'.repeat(30));
            expect(vi.mocked(axios.post).mock.calls.map(([url]) => url)).toEqual([
                '/api/pushover/credentials',
            ]);
            // The staged document can be saved/applied by the parent and reopened normally.
        }
    );
    it('reopens masked credentials and previews without sending or generating a summary', async () => {
        const changed = vi.fn();
        render(
            <HTTPToolForm
                phase="in_call"
                config={{
                    notify_sales: {
                        kind: 'pushover',
                        phase: 'in_call',
                        enabled: true,
                        is_global: false,
                        token_file: '/managed/application',
                        key_file: '/managed/recipient',
                        description: 'Use for Sales',
                    },
                }}
                onChange={changed}
                presentation="page"
            />
        );
        fireEvent.click(screen.getByRole('button', { name: 'Configure notify_sales' }));
        expect(screen.getByLabelText('Application token')).toHaveValue('');
        expect(screen.getByLabelText('User/group key')).toHaveValue('');
        fireEvent.click(screen.getByRole('button', { name: 'Preview message' }));
        await screen.findByText('Synthetic preview');
        expect(vi.mocked(axios.post).mock.calls.map(([url]) => url)).toEqual([
            '/api/pushover/preview',
        ]);
        fireEvent.click(screen.getByRole('button', { name: 'Update tool' }));
        await waitFor(() => expect(changed).toHaveBeenCalledOnce());
        expect(changed.mock.calls[0][0].notify_sales.token_file).toBe('/managed/application');
    });
});
