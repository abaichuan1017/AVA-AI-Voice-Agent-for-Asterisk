// @vitest-environment jsdom
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import '@testing-library/jest-dom/vitest';
import axios from 'axios';
import CalendlyToolForm from './CalendlyToolForm';

vi.mock('../../auth/AuthContext', () => ({ useAuth: () => ({ token: 'synthetic-admin-token' }) }));
vi.mock('axios', () => ({ default: { get: vi.fn(), request: vi.fn(), isAxiosError: () => false } }));

const connection = { id: 'a'.repeat(32), revision: 1, name: 'Alex', timezone: 'UTC', enabled: true, qualified: false };
const entry = { uri: 'https://api.calendly.com/event_types/demo', name: 'Demo', host: 'Alex', duration: 30, supported: true, reason: '', location_summary: 'Office' };
const binding = { uri: entry.uri, connection_id: connection.id, name: 'Demo', duration: 30, one_to_one_confirmed: true };

function mock(connected = true) {
    vi.mocked(axios.get).mockResolvedValue({ data: connected ? connection : { revision: 0, enabled: false } });
    vi.mocked(axios.request).mockImplementation(async request => {
        const path = request.url || '';
        if (path.endsWith('/connection/verify')) return { data: { name: 'Alex' } };
        if (path.endsWith('/connection')) return { data: connection };
        if (path.endsWith('/appointment-types')) return { data: [entry, { ...entry, uri: entry.uri + 'group', name: 'Group event', supported: false, reason: 'Group events are unsupported' }] };
        if (path.endsWith('/test-proposals')) return { data: { operation: 'setup:00000000-0000-4000-8000-000000000000', appointment_name: 'Demo', duration_minutes: 30, timezone: 'UTC', location_summary: 'Office', questions: [], slots: [{ slot_reference: 'slot-ref', start_datetime: '2030-01-01T12:00:00Z' }] } };
        if (path.endsWith('/test-bookings')) return { data: { status: 'pending', reservation_status: 'uncertain', message: 'Confirmation pending; ask staff', attempt_reference: 'attempt' } };
        return { data: [] };
    });
}

afterEach(() => { cleanup(); vi.resetAllMocks(); sessionStorage.clear(); });

describe('Calendly guided setup', () => {
    it('masks a candidate token, verifies without saving, and invalidates verification after edits', async () => {
        mock(false);
        render(<CalendlyToolForm config={{}} onChange={vi.fn()} onSave={vi.fn()} />);
        await screen.findByText('Not connected');
        const candidate = screen.getByLabelText('Personal access token');
        expect(candidate).toHaveAttribute('type', 'password');
        await userEvent.type(candidate, 'private-synthetic-pat');
        await userEvent.click(screen.getByRole('button', { name: 'Verify connection' }));
        await screen.findByText(/Connection verified for Alex/);
        expect(screen.getByRole('button', { name: 'Save connection' })).toBeEnabled();
        expect(vi.mocked(axios.request).mock.calls.every(([r]) => r.method !== 'PUT')).toBe(true);
        await userEvent.type(candidate, '-edited');
        expect(screen.getByRole('button', { name: 'Save connection' })).toBeDisabled();
        expect(screen.queryByText('private-synthetic-pat')).not.toBeInTheDocument();
    });

    it('requires one-to-one acknowledgement and keeps unsupported types unselectable', async () => {
        mock();
        const changed = vi.fn();
        render(<CalendlyToolForm config={{}} onChange={changed} onSave={vi.fn()} />);
        await screen.findByText(/Alex · UTC/);
        await userEvent.click(screen.getByRole('button', { name: 'Refresh from Calendly' }));
        const demo = await screen.findByLabelText(/Demo · Alex/);
        expect(demo).toBeDisabled();
        await userEvent.click(screen.getByLabelText(/I checked that selected appointments/));
        expect(demo).toBeEnabled();
        expect(screen.getByLabelText(/Group event · Alex/)).toBeDisabled();
        await userEvent.click(demo);
        expect(changed.mock.calls[0][0].appointments.demo.connection_id).toBe(connection.id);
    });

    it('requires final approval, invalidates it on detail changes, and never automatically repeats a pending write', async () => {
        mock();
        function Form() {
            const [cfg, setCfg] = useState({ enabled: true, appointments: { demo: binding } });
            return <CalendlyToolForm config={cfg} onChange={v => setCfg(v as typeof cfg)} onSave={async () => {}} />;
        }
        render(<Form />);
        await screen.findByText(/Alex · UTC/);
        await userEvent.selectOptions(screen.getByLabelText('Appointment'), 'demo');
        await userEvent.click(screen.getByRole('button', { name: 'Find test times' }));
        await userEvent.selectOptions(await screen.findByLabelText('Available time'), 'slot-ref');
        await userEvent.type(screen.getByLabelText('Test invitee name'), 'Test');
        await userEvent.type(screen.getByLabelText('Test recipient email'), 'test@example.com');
        const submit = screen.getByRole('button', { name: 'Create test booking and send invitation' });
        expect(submit).toBeDisabled();
        await userEvent.click(screen.getByLabelText(/I approve these details/));
        expect(submit).toBeEnabled();
        fireEvent.change(screen.getByLabelText('Test recipient email'), { target: { value: 'corrected@example.com' } });
        await waitFor(() => expect(submit).toBeDisabled());
        await userEvent.click(screen.getByLabelText(/I approve these details/));
        await userEvent.click(submit);
        await screen.findByText(/Confirmation pending; ask staff/);
        expect(submit).toBeDisabled();
        expect(vi.mocked(axios.request).mock.calls.filter(([r]) => r.url?.endsWith('/test-bookings'))).toHaveLength(1);
    });
});
