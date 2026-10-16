/**
 * What the second review of M7 found in the charts, kept fixed: each test fails on the
 * defect it names.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { LineChart, type LineSeries } from './charts/LineChart';
import { RangeDotChart } from './charts/RangeDotChart';

/** Charts measured at `width` (jsdom has no layout: the ResizeObserver reports it). */
function atWidth(width: number) {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      readonly callback: ResizeObserverCallback;
      constructor(callback: ResizeObserverCallback) {
        this.callback = callback;
      }
      observe() {
        this.callback([{ contentRect: { width } } as ResizeObserverEntry], this);
      }
      unobserve() {}
      disconnect() {}
    },
  );
}

const DAY = 86_400_000;

function days(count: number): LineSeries {
  return {
    id: 'observed',
    label: 'Observed',
    color: 'var(--series-1)',
    points: Array.from({ length: count }, (_, i) => ({ x: Date.UTC(2023, 0, 1) + i * DAY, y: i })),
  };
}

/** The x-axis tick labels (centred under their tick; the y labels are end-anchored). */
function xTickLabels(container: HTMLElement): string[] {
  return [...container.querySelectorAll('.axis text[text-anchor="middle"]')].map(
    (text) => text.textContent,
  );
}

describe('a chart reading', () => {
  it('is dropped when the data changes under a still pointer, never shown at a day not in it', () => {
    const props = {
      label: 'History',
      summary: 's',
      formatValue: String,
      formatTick: String,
      formatX: (x: number) => new Date(x).toISOString().slice(0, 10),
    };
    const { container, rerender } = render(<LineChart {...props} series={[days(1308)]} />);
    const svg = container.querySelector('svg.chart-svg')!;
    fireEvent.pointerMove(svg, { clientX: 600, clientY: 10 });
    const slider = screen.getByRole('slider');
    expect(slider.getAttribute('aria-valuetext')).toMatch(/^2026-/);
    // A shorter window arrives while the pointer rests where the old data's end was.
    rerender(<LineChart {...props} series={[days(120)]} />);
    expect(slider).toHaveAttribute('aria-valuetext', 'No value selected');
    expect(slider).toHaveAttribute('aria-valuenow', '0');
    expect(container.querySelector('.tooltip')).toBeNull();
    expect(container.textContent).not.toContain('1970');
  });
});

describe('the horizon chart on a phone', () => {
  const horizons = (id: string, style: LineSeries['style'], offset: number): LineSeries => ({
    id,
    label: id,
    color: 'c',
    style,
    markers: true,
    points: Array.from({ length: 30 }, (_, i) => ({ x: i + 1, y: 1000 + offset + i * 10 })),
  });
  const chart = (series: LineSeries[]) => (
    <LineChart
      label="Accuracy by horizon"
      summary="s"
      series={series}
      xType="linear"
      formatValue={String}
      formatTick={String}
      formatX={String}
    />
  );

  it('keeps more than one tick on its numeric x axis', () => {
    atWidth(320);
    const { container } = render(chart([horizons('a', 'solid', 0)]));
    expect(xTickLabels(container)).toEqual(['10', '20', '30']);
  });

  it('leaves out the point markers that would hide the line patterns of several series', () => {
    atWidth(320);
    const { container } = render(
      chart([horizons('a', 'solid', 0), horizons('b', 'dashed', 50), horizons('c', 'dotted', 90)]),
    );
    expect(container.querySelectorAll('circle.marker')).toHaveLength(0);
    for (const style of ['solid', 'dashed', 'dotted'])
      expect(container.querySelector(`path.series-line[data-style="${style}"]`)).not.toBeNull();
  });

  it('keeps the markers of a single series, and of several with room between points', () => {
    atWidth(320);
    const single = render(chart([horizons('a', 'solid', 0)]));
    expect(single.container.querySelectorAll('circle.marker')).toHaveLength(30);
    single.unmount();
    atWidth(1280);
    const wide = render(chart([horizons('a', 'solid', 0), horizons('b', 'dashed', 50)]));
    expect(wide.container.querySelectorAll('circle.marker')).toHaveLength(60);
  });
});

describe('chart labels', () => {
  it('give the interval chart room for its row labels on a phone', () => {
    atWidth(300);
    const { container } = render(
      <RangeDotChart
        label="Coverage"
        summary="s"
        domain={[0.5, 1]}
        formatValue={String}
        rows={['80 % interval', '95 % interval'].map((label, i) => ({
          id: label,
          label,
          value: 0.8 + i * 0.1,
          low: 0.7,
          high: 0.95,
          target: 0.8 + i * 0.15,
          tolerance: 0.05,
        }))}
      />,
    );
    for (const text of container.querySelectorAll('text.bar-category')) {
      // End-anchored: the label (12 px text, under 7 px a character) starts inside the chart.
      const end = Number(text.getAttribute('x'));
      expect(end - text.textContent.length * 7).toBeGreaterThanOrEqual(0);
    }
  });

  it('put a rule label on the side of its line with room for it', () => {
    const label = 'Forecast origin Jun 30, 2026';
    const at = (x: number) => {
      const { container, unmount } = render(
        <LineChart
          label="T"
          summary="s"
          series={[
            {
              id: 'a',
              label: 'A',
              color: 'c',
              points: Array.from({ length: 101 }, (_, i) => ({ x: i, y: i })),
            },
          ]}
          rules={[{ x, label }]}
          xType="linear"
          formatValue={String}
          formatTick={String}
          formatX={String}
        />,
      );
      const anchor = container.querySelector('text.rule-label')!.getAttribute('text-anchor');
      unmount();
      return anchor;
    };
    // About 140 px left of the right edge: too little for the label after the line.
    expect(at(78)).toBe('end');
    expect(at(10)).toBe('start');
  });
});

describe('lines over their bands', () => {
  const series: LineSeries[] = [
    days(30),
    { ...days(30), id: 'projected', label: 'Projected', style: 'dashed' },
  ];
  const band = {
    id: 'band',
    label: 'Band',
    fill: 'var(--series-2-wash)',
    points: series[0]!.points.map((p) => ({ x: p.x, low: 0, high: 40 })),
  };
  const chart = (bands: (typeof band)[]) => (
    <LineChart
      label="Projection"
      summary="s"
      series={series}
      bands={bands}
      formatValue={String}
      formatTick={String}
      formatX={String}
    />
  );

  it('run on a surface-coloured halo, under every line, where a band is drawn', () => {
    const { container, rerender } = render(chart([band]));
    const paths = [...container.querySelectorAll('path.series-halo, path.series-line')];
    expect(paths.map((path) => path.getAttribute('class'))).toEqual([
      'series-halo',
      'series-halo',
      'series-line',
      'series-line',
    ]);
    expect(paths[0]!.getAttribute('d')).toBe(paths[2]!.getAttribute('d'));
    expect(paths[1]!.getAttribute('d')).toBe(paths[3]!.getAttribute('d'));
    rerender(chart([]));
    expect(container.querySelectorAll('path.series-halo')).toHaveLength(0);
  });

  it('reach 3:1 against the surface the halo shows, in both themes', () => {
    // What the halo relies on: each series colour against the panel surface (WCAG 1.4.11).
    const tokens = readFileSync(join(import.meta.dirname, 'styles', 'tokens.css'), 'utf-8');
    const themes = tokens.split('@media (prefers-color-scheme: dark)');
    expect(themes).toHaveLength(2);
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map((at) => {
        const c = parseInt(hex.slice(at, at + 2), 16) / 255;
        return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
      });
      return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
    };
    for (const theme of themes) {
      const token = (name: string) => new RegExp(`--${name}: (#[0-9a-f]{6});`).exec(theme)![1]!;
      const surface = luminance(token('surface'));
      for (const name of ['series-1', 'series-2', 'series-3']) {
        const colour = luminance(token(name));
        const ratio = (Math.max(colour, surface) + 0.05) / (Math.min(colour, surface) + 0.05);
        expect(ratio, name).toBeGreaterThanOrEqual(3);
      }
    }
  });
});
