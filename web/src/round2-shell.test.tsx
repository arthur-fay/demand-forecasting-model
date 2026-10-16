/**
 * What the second review of M7 found in the dashboard's shell and simulator, kept fixed:
 * each test fails on the defect it names.
 */
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { App } from './App';
import { EquallyScoredTag } from './components/Tag';
import { apiError, DESCRIPTORS, FIXTURES, json, mockApi } from './test/api';

function at(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}

async function heading(name: RegExp | string, level = 1) {
  return screen.findByRole('heading', { name, level }, { timeout: 5000 });
}

async function openSimulator(descriptor: unknown = DESCRIPTORS.local, overrides = {}) {
  mockApi(overrides, descriptor);
  const view = at('/simulator');
  await heading('Hypothetical simulator');
  await screen.findByRole('heading', { name: 'Hypothetical inputs' });
  return view;
}

/** A simulation that never answers until it is aborted. */
function pending(_url: URL, init: RequestInit | undefined): Promise<Response> {
  return new Promise((_resolve, reject) => {
    init?.signal?.addEventListener('abort', () => {
      reject(new DOMException('aborted', 'AbortError'));
    });
  });
}

describe('the phone navigation menu', () => {
  afterEach(() => {
    document.documentElement.style.removeProperty('--topbar-height');
  });

  it('opens under the top bar, whose height the page measures', () => {
    mockApi();
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(99);
    vi.stubGlobal(
      'ResizeObserver',
      class {
        readonly callback: ResizeObserverCallback;
        constructor(callback: ResizeObserverCallback) {
          this.callback = callback;
        }
        observe() {
          this.callback([], this);
        }
        unobserve() {}
        disconnect() {}
      },
    );
    at('/');
    expect(document.documentElement.style.getPropertyValue('--topbar-height')).toBe('99px');
  });

  it('leaves Escape pressed elsewhere to the control that has it', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/');
    const slider = await screen.findByRole(
      'slider',
      { name: /Observed history and the archived 30-day projection/ },
      { timeout: 5000 },
    );
    const button = screen.getByRole('button', { name: 'Open navigation' });
    await user.click(button);
    fireEvent.keyDown(slider, { key: 'Escape' });
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(document.activeElement).toBe(button);
  });

  it('closes on Escape from within it, back on its button', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/');
    await heading(/Decision-intelligence research/);
    const button = screen.getByRole('button', { name: 'Open navigation' });
    await user.click(button);
    const nav = screen.getByRole('navigation', { name: 'Dashboard sections' });
    within(nav).getAllByRole('link')[1]!.focus();
    await user.keyboard('{Escape}');
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(document.activeElement).toBe(button);
  });

  it('closes when the focus moves on into the page it covers', async () => {
    mockApi();
    const user = userEvent.setup();
    at('/');
    const slider = await screen.findByRole(
      'slider',
      { name: /Observed history and the archived 30-day projection/ },
      { timeout: 5000 },
    );
    const button = screen.getByRole('button', { name: 'Open navigation' });
    await user.click(button);
    act(() => {
      slider.focus();
    });
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(document.activeElement).toBe(slider);
  });
});

describe('the deployment badge', () => {
  it('says when a local API (or the SSH tunnel) cannot run simulations from the browser', async () => {
    mockApi({}, DESCRIPTORS.localWithKey);
    at('/provenance');
    expect(await screen.findByText('Local API · read-only')).toBeInTheDocument();
  });
});

describe('the simulator', () => {
  it('keeps the list of changes out of the sticky action bar', async () => {
    await openSimulator();
    const summary = screen.getByText(/No change: this runs the reference configuration/);
    expect(summary.closest('.sim-actions')).toBeNull();
  });

  it('keeps the focus through a run and its cancellation, and says it was cancelled', async () => {
    const user = userEvent.setup();
    await openSimulator(DESCRIPTORS.local, { '/api/v1/simulations': pending });
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    const running = await screen.findByRole('button', { name: 'Running…' });
    expect(running).toHaveAttribute('aria-disabled', 'true');
    expect(running).toBeEnabled();
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    const run = screen.getByRole('button', { name: 'Run simulation' });
    await waitFor(() => {
      expect(document.activeElement).toBe(run);
    });
    expect(screen.getAllByText('Simulation cancelled.')).toHaveLength(2); // seen and heard
  });

  it('runs once only while its run is on the server', async () => {
    const user = userEvent.setup();
    let calls = 0;
    await openSimulator(DESCRIPTORS.local, {
      '/api/v1/simulations': (url: URL, init: RequestInit | undefined) => {
        calls += 1;
        return pending(url, init);
      },
    });
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    await user.click(await screen.findByRole('button', { name: 'Running…' }));
    expect(calls).toBe(1);
  });

  it('names each reference assumption after its field, and invites no edit when read-only', async () => {
    const { container } = await openSimulator(DESCRIPTORS.publicDemo);
    const summaries = [
      ...container.querySelectorAll('.simulator-form details.field-help > summary'),
    ].map((summary) => summary.textContent);
    expect(summaries).toHaveLength(FIXTURES.parameters.data.parameters.length);
    expect(summaries).toContain('Reference assumption for Initial backlog');
    expect(new Set(summaries).size).toBe(summaries.length);
    expect(screen.queryByText(/change what you want to explore/)).not.toBeInTheDocument();
    expect(screen.queryByText(/No change: this runs/)).not.toBeInTheDocument();
    // The read-only message points to no result "below": there is none on this page.
    expect(screen.getByText(/Every reference result remains available/)).toBeInTheDocument();
    expect(screen.queryByText(/results below/)).not.toBeInTheDocument();
  });

  it('names both demand paths in its warning, as the API does', async () => {
    await openSimulator();
    const warning = screen
      .getByText('Simulations are exploratory, not operational advice')
      .closest('.callout')!;
    expect(warning).toHaveTextContent(
      'The demand is an unvalidated point forecast or, if chosen, a per-day M3 quantile stress path from the FAILED_CALIBRATION intervals, which carries no probability.',
    );
  });

  it('keeps the sources of its reference values, also in the read-only demo', async () => {
    await openSimulator(DESCRIPTORS.publicDemo);
    const form = screen.getByRole('form', { name: 'Hypothetical inputs' });
    expect(within(form).getByText('Sources and fingerprints')).toBeInTheDocument();
    const fingerprint = FIXTURES.parameters.provenance.data_fingerprint;
    expect(within(form).getByText(fingerprint)).toBeInTheDocument();
  });

  it('never says a run the server may have completed was not run', async () => {
    const user = userEvent.setup();
    await openSimulator(DESCRIPTORS.local, { '/api/v1/simulations': () => json({ data: {} }) });
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(
      await screen.findByText('Simulation result not shown: Unexpected answer from the API'),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Simulation not run/)).not.toBeInTheDocument();
  });

  it('says a run the API refused was not run', async () => {
    const user = userEvent.setup();
    await openSimulator(DESCRIPTORS.local, {
      '/api/v1/simulations': () => apiError(503, 'simulations_busy', 'Every worker is busy.'),
    });
    await user.click(screen.getByRole('button', { name: 'Run simulation' }));
    expect(await screen.findByText('Simulation not run: The server is busy')).toBeInTheDocument();
  });
});

describe('Retry', () => {
  it('keeps the focus in the section it was pressed in', async () => {
    const user = userEvent.setup();
    let fail = true;
    mockApi({
      '/api/v1/forecasts/history': () =>
        fail ? apiError(503, 'artifact_unavailable', 'not ready') : json(FIXTURES.history),
    });
    at('/');
    await heading(/Decision-intelligence research/);
    const retry = (await screen.findAllByRole('button', { name: 'Retry' }))[0]!;
    const section = retry.closest('section')!;
    const title = document.getElementById(section.getAttribute('aria-labelledby')!)!;
    fail = false;
    await user.click(retry);
    expect(document.activeElement).toBe(title);
    await waitFor(() => {
      expect(screen.queryAllByRole('button', { name: 'Retry' })).toHaveLength(0);
    });
    expect(document.activeElement).toBe(title);
  });
});

describe('the equally scored tag', () => {
  it('fits a strict optimum and a relaxed DIAGNOSTIC plan alike', () => {
    render(<EquallyScoredTag unique={false} />);
    const tag = screen.getByText('One of several equally scored');
    expect(tag).toHaveAttribute(
      'title',
      'Other plans score the same under these HYPOTHETICAL values: one of several equally scored HYPOTHETICAL plans',
    );
  });
});
