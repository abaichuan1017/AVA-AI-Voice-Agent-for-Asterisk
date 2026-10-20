// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import GoogleSessionRecoveryFields from './GoogleSessionRecoveryFields';

afterEach(cleanup);

describe('Google Live session recovery settings', () => {
    it('keeps both options off for existing saved configurations', () => {
        render(<GoogleSessionRecoveryFields config={{}} onChange={vi.fn()} />);
        expect((screen.getByLabelText('Keep long calls connected') as HTMLInputElement).checked).toBe(false);
        expect((screen.getByLabelText('Compress long conversation history') as HTMLInputElement).checked).toBe(false);
        expect(screen.queryByLabelText('Maximum attempts')).toBeNull();
    });

    it('persists the independent switches and recovery limits', () => {
        const onChange = vi.fn();
        render(<GoogleSessionRecoveryFields config={{ session_resumption_enabled: true }} onChange={onChange} />);
        fireEvent.click(screen.getByLabelText('Compress long conversation history'));
        expect(onChange).toHaveBeenLastCalledWith('context_window_compression_enabled', true);
        fireEvent.change(screen.getByLabelText('Recovery time limit (seconds)'), { target: { value: '8' } });
        expect(onChange).toHaveBeenLastCalledWith('session_recovery_timeout_sec', 8);
        fireEvent.change(screen.getByLabelText('Maximum attempts'), { target: { value: '3' } });
        expect(onChange).toHaveBeenLastCalledWith('session_recovery_max_attempts', 3);
        fireEvent.change(screen.getByLabelText('Caller audio buffer (milliseconds)'), { target: { value: '500' } });
        expect(onChange).toHaveBeenLastCalledWith('session_recovery_audio_buffer_ms', 500);
        fireEvent.click(screen.getByLabelText('Keep long calls connected'));
        expect(onChange).toHaveBeenLastCalledWith('session_resumption_enabled', false);
    });
});
