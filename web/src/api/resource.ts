/**
 * A small cache of API reads shared by every view.
 *
 * Each read has a key (its URL). The first view that needs it starts the request; others
 * share it; a successful answer is kept for the session (the API serves committed,
 * immutable results: `GET` bodies are byte-identical between calls). Failures are not
 * kept, so "Retry" really retries. A request that takes longer than `SLOW_MS` is marked
 * slow, so views can say so instead of spinning silently.
 *
 * Groups keep the last successful value of related keys (for example the history at
 * several windows), so a chart can hold its previous frame while a new window loads.
 */
import { useEffect, useRef, useSyncExternalStore } from 'react';

export const SLOW_MS = 2_500;

export type Snapshot<T> =
  | { readonly status: 'idle' }
  | { readonly status: 'loading'; readonly slow: boolean }
  | { readonly status: 'success'; readonly value: T }
  | { readonly status: 'error'; readonly error: unknown };

const IDLE: Snapshot<never> = { status: 'idle' };

export class ResourceStore {
  private readonly entries = new Map<string, Snapshot<unknown>>();
  private readonly latest = new Map<string, unknown>();
  private readonly listeners = new Set<() => void>();
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(private readonly slowMs = SLOW_MS) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  get<T>(key: string | null): Snapshot<T> {
    if (key === null) return IDLE;
    return (this.entries.get(key) as Snapshot<T> | undefined) ?? IDLE;
  }

  latestOf(group: string | undefined): unknown {
    return group === undefined ? undefined : this.latest.get(group);
  }

  /** Start loading `key` unless it is loading or loaded. */
  ensure<T>(key: string, load: () => Promise<T>, group?: string): void {
    const current = this.entries.get(key);
    if (current && (current.status === 'loading' || current.status === 'success')) return;
    this.set(key, { status: 'loading', slow: false });
    this.timers.set(
      key,
      setTimeout(() => {
        if (this.entries.get(key)?.status === 'loading')
          this.set(key, { status: 'loading', slow: true });
      }, this.slowMs),
    );
    load().then(
      (value) => {
        this.clearTimer(key);
        if (group !== undefined) this.latest.set(group, value);
        this.set(key, { status: 'success', value });
      },
      (error: unknown) => {
        this.clearTimer(key);
        this.set(key, { status: 'error', error });
      },
    );
  }

  /** Forget a failed (or any) key, so the next `ensure` loads it again. */
  invalidate(key: string): void {
    this.clearTimer(key);
    this.entries.delete(key);
    this.notify();
  }

  clear(): void {
    for (const key of [...this.timers.keys()]) this.clearTimer(key);
    this.entries.clear();
    this.latest.clear();
    this.notify();
  }

  private clearTimer(key: string): void {
    const timer = this.timers.get(key);
    if (timer !== undefined) clearTimeout(timer);
    this.timers.delete(key);
  }

  private set(key: string, snapshot: Snapshot<unknown>): void {
    this.entries.set(key, snapshot);
    this.notify();
  }

  private notify(): void {
    for (const listener of [...this.listeners]) listener();
  }
}

export const store = new ResourceStore();

export interface Resource<T> {
  readonly snapshot: Snapshot<T>;
  /** The value of this key, if loaded. */
  readonly data: T | undefined;
  /** The last value loaded in this key's group (to hold a chart's frame while loading). */
  readonly previous: T | undefined;
  readonly retry: () => void;
}

/** Read `key` through the shared store (`null`: nothing to read yet). */
export function useResource<T>(
  key: string | null,
  load: () => Promise<T>,
  options: { group?: string; store?: ResourceStore } = {},
): Resource<T> {
  const target = options.store ?? store;
  const loader = useRef(load);
  useEffect(() => {
    loader.current = load;
  });
  const snapshot = useSyncExternalStore(
    target.subscribe,
    () => target.get<T>(key),
    () => target.get<T>(key),
  );
  const previous = useSyncExternalStore(
    target.subscribe,
    () => target.latestOf(options.group),
    () => target.latestOf(options.group),
  ) as T | undefined;
  const { group } = options;
  useEffect(() => {
    if (key !== null) target.ensure(key, () => loader.current(), group);
  }, [key, group, target]);
  return {
    snapshot,
    data: snapshot.status === 'success' ? snapshot.value : undefined,
    previous,
    retry: () => {
      if (key === null) return;
      target.invalidate(key);
      target.ensure(key, () => loader.current(), group);
    },
  };
}
