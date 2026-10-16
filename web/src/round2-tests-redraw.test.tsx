/**
 * Moving the reading of a chart re-renders its crosshair only (docs/DASHBOARD.md section
 * 16: every pointer move redrew the whole chart, 24 ms at 4x CPU slowdown on 1,278 points).
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { line } from 'd3-shape';
import { describe, expect, it, vi } from 'vitest';

import { LineChart } from './charts/LineChart';

// Count the line paths the chart builds.
vi.mock('d3-shape', async (importOriginal) => {
  const actual = await importOriginal<typeof import('d3-shape')>();
  return { ...actual, line: vi.fn(actual.line) };
});

describe('a chart reading', () => {
  it('moves without building the lines again', () => {
    const points = Array.from({ length: 1200 }, (_, x) => ({ x, y: (x * 37) % 101 }));
    const { container } = render(
      <LineChart
        label="Test"
        summary="A long test series"
        series={[{ id: 'a', label: 'Observed', color: 'var(--series-1)', points }]}
        xType="linear"
        formatValue={String}
        formatTick={String}
        formatX={(x) => `x=${x}`}
      />,
    );
    const built = vi.mocked(line).mock.calls.length;
    expect(built).toBeGreaterThan(0);
    const svg = container.querySelector('svg.chart-svg')!;
    const slider = screen.getByRole('slider');
    for (const clientX of [100, 200, 300, 400]) {
      fireEvent.pointerMove(svg, { clientX, clientY: 10 });
      expect(slider.getAttribute('aria-valuetext')).toMatch(/^x=\d+\. Observed: \d+/);
    }
    fireEvent.keyDown(slider, { key: 'ArrowRight' });
    expect(vi.mocked(line).mock.calls.length).toBe(built);
  });
});
