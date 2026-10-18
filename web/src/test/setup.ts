import '@testing-library/jest-dom/vitest';
import { cleanup, configure } from '@testing-library/react';
import { afterEach, vi } from 'vitest';

import { store } from '../api/resource';

// A page's first render (a lazy import, then its data) can take more than Testing
// Library's default 1 s on a loaded machine: every findBy* and waitFor waits up to 5 s,
// as the explicit waits in the tests already did (the per-test limit is 20 s).
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  store.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  window.history.replaceState(null, '', '/');
});
