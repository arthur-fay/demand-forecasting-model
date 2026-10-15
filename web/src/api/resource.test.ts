import { describe, expect, it, vi } from 'vitest';

import { ResourceStore } from './resource';

describe('ResourceStore', () => {
  it('shares one request per key and keeps the answer', async () => {
    const store = new ResourceStore();
    const load = vi.fn(() => Promise.resolve(42));
    store.ensure('k', load);
    store.ensure('k', load);
    expect(load).toHaveBeenCalledTimes(1);
    await vi.waitFor(() => {
      expect(store.get('k')).toEqual({ status: 'success', value: 42 });
    });
    store.ensure('k', load);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('does not keep failures: a retry asks again', async () => {
    const store = new ResourceStore();
    const load = vi.fn().mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce('up');
    store.ensure('k', load);
    await vi.waitFor(() => {
      expect(store.get('k').status).toBe('error');
    });
    store.invalidate('k');
    store.ensure('k', load);
    await vi.waitFor(() => {
      expect(store.get('k')).toEqual({ status: 'success', value: 'up' });
    });
  });

  it('marks a slow request after the threshold', async () => {
    vi.useFakeTimers();
    const store = new ResourceStore(1000);
    store.ensure('k', () => new Promise(() => undefined));
    expect(store.get('k')).toEqual({ status: 'loading', slow: false });
    await vi.advanceTimersByTimeAsync(1001);
    expect(store.get('k')).toEqual({ status: 'loading', slow: true });
  });

  it('keeps the last value of a group for a chart to hold its frame', async () => {
    const store = new ResourceStore();
    store.ensure('a', () => Promise.resolve('first'), 'history');
    await vi.waitFor(() => {
      expect(store.latestOf('history')).toBe('first');
    });
    store.ensure('b', () => new Promise(() => undefined), 'history');
    expect(store.latestOf('history')).toBe('first');
  });
});
