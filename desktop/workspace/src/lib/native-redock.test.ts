import { beforeEach, expect, it, vi } from 'vitest';
const bridge = vi.hoisted(() => ({ invoke: vi.fn(), releases: [] as ReturnType<typeof vi.fn>[], listeners: new Map<string, (event: { payload: { id: string } }) => void>() }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: bridge.invoke, isTauri: () => true }));
vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => ({ label: 'workspace-chat-session-1-0', listen: async (event: string, handler: (event: { payload: { id: string } }) => void) => { bridge.listeners.set(event, handler); const release = vi.fn(); bridge.releases.push(release); return release; } }) }));
import { commitRedock, requestRedock } from './native';
beforeEach(() => { bridge.invoke.mockReset(); bridge.listeners.clear(); bridge.releases.length = 0; });
it('buffers an early acknowledgement and never closes before the source explicitly commits', async () => {
  bridge.invoke.mockImplementation(async (command: string) => {
    if (command === 'workspace_redock') { bridge.listeners.get('termx-native-redock-accepted')?.({ payload: { id: 'receipt' } }); return 'receipt'; }
  });
  const receipt = await requestRedock('session-1', 'chat', { version: 1 });
  expect(receipt).toBe('receipt');
  expect(bridge.invoke.mock.calls.map(call => call[0])).toEqual(['workspace_redock']);
  expect(bridge.releases.every(release => release.mock.calls.length === 1)).toBe(true);
  await commitRedock(receipt);
  expect(bridge.invoke).toHaveBeenLastCalledWith('workspace_redock_commit', { id: 'receipt' });
});
it('cancelled handoffs reject, release listeners and never issue a close commitment', async () => {
  bridge.invoke.mockImplementation(async (command: string) => {
    if (command === 'workspace_redock') { bridge.listeners.get('termx-native-redock-cancelled')?.({ payload: { id: 'receipt' } }); return 'receipt'; }
  });
  await expect(requestRedock('session-1', 'chat', {})).rejects.toThrow('remains open');
  expect(bridge.invoke.mock.calls.map(call => call[0])).toEqual(['workspace_redock', 'workspace_redock_cancel']);
  expect(bridge.releases.every(release => release.mock.calls.length === 1)).toBe(true);
});
