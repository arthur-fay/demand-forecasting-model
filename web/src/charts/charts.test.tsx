import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';

import { BarChart, barPath } from './BarChart';
import { leftMargin, tooltipText } from './core';
import { LineChart } from './LineChart';
import { RangeDotChart } from './RangeDotChart';
import { StackedColumns } from './StackedColumns';

describe('LineChart', () => {
  const series = [
    {
      id: 'a',
      label: 'Observed',
      color: 'var(--series-1)',
      points: [
        { x: 1, y: 10 },
        { x: 2, y: null },
        { x: 3, y: 30 },
      ],
    },
    {
      id: 'b',
      label: 'Projected',
      color: 'var(--series-2)',
      style: 'dashed' as const,
      points: [
        { x: 3, y: 30 },
        { x: 4, y: 40 },
      ],
    },
  ];

  it('lists every series at the pointer, including a day not recorded', () => {
    const { container } = render(
      <LineChart
        label="Test"
        summary="A test chart"
        series={series}
        bands={[{ id: 'band', label: 'Band', fill: 'red', points: [{ x: 4, low: 35, high: 45 }] }]}
        rules={[{ x: 3, label: 'Origin' }]}
        levels={[{ y: 20, label: 'Target' }]}
        marks={[{ x: 1, y: 10, label: 'Holiday' }]}
        xType="linear"
        formatValue={(v) => `${v} u`}
        formatTick={String}
        formatX={(x) => `x=${x}`}
        notesAt={(x) => (x === 4 ? ['projected'] : [])}
      />,
    );
    const svg = container.querySelector('svg.chart-svg')!;
    fireEvent.pointerMove(svg, { clientX: 2000, clientY: 10 });
    const slider = screen.getByRole('slider');
    expect(slider.getAttribute('aria-valuetext')).toBe(
      'x=4. Projected: 40 u. Band: 35 u – 45 u. projected',
    );
    fireEvent.pointerMove(svg, { clientX: 0, clientY: 10 });
    expect(slider.getAttribute('aria-valuetext')).toContain('Holiday');
    fireEvent.pointerLeave(svg);
    expect(slider).toHaveAttribute('aria-valuenow', '0');
    expect(screen.getByText('Origin')).toBeInTheDocument();
    expect(screen.getByText('Target')).toBeInTheDocument();
  });

  it('says a missing observation is not recorded', async () => {
    render(
      <LineChart
        label="T"
        summary="s"
        series={series}
        xType="linear"
        formatValue={String}
        formatTick={String}
        formatX={String}
      />,
    );
    const slider = screen.getByRole('slider');
    slider.focus();
    await userEvent.keyboard('{ArrowRight}{ArrowRight}');
    expect(slider.getAttribute('aria-valuetext')).toContain('Observed: not recorded');
    await userEvent.keyboard('{ArrowLeft}');
    expect(slider).toHaveAttribute('aria-valuenow', '1');
  });

  it('draws a time axis and a calibration diagonal', () => {
    const { container } = render(
      <LineChart
        label="T"
        summary="s"
        series={[
          {
            id: 'a',
            label: 'A',
            color: 'c',
            markers: true,
            points: [
              { x: Date.UTC(2026, 0, 1), y: 1 },
              { x: Date.UTC(2026, 11, 1), y: 2 },
            ],
          },
        ]}
        diagonal
        formatValue={String}
        formatTick={String}
        formatX={String}
      />,
    );
    expect(container.querySelectorAll('circle.marker')).toHaveLength(2);
    expect(container.querySelector('line[stroke-dasharray="2 3"]')).not.toBeNull();
  });
});

describe('BarChart', () => {
  it('grows negative values left of the baseline and reads every bar', async () => {
    render(
      <BarChart
        label="Differences"
        summary="s"
        valueLabel="Difference"
        formatValue={(v) => `${v}`}
        rows={[
          { id: 'a', label: 'A', value: -5, color: 'c', notes: ['note'] },
          { id: 'b', label: 'B', value: null, color: 'c', suffix: 'DIAGNOSTIC' },
          { id: 'c', label: 'C', value: 10, color: 'c' },
        ]}
      />,
    );
    const slider = screen.getByRole('slider');
    expect(slider).toHaveAttribute('aria-orientation', 'vertical');
    slider.focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(slider.getAttribute('aria-valuetext')).toBe('A. Difference: -5. note');
    await userEvent.keyboard('{ArrowDown}');
    expect(slider.getAttribute('aria-valuetext')).toBe('B. Difference: not reported');
    await userEvent.keyboard('{End}');
    expect(slider).toHaveAttribute('aria-valuenow', '3');
    expect(screen.getAllByText(/not reported/).length).toBeGreaterThan(0);
  });

  it('rounds only the data end of a bar', () => {
    expect(barPath(0, 100, 0, 18)).toMatch(/^M0,0H96Q100,0 100,4/);
    expect(barPath(100, 0, 0, 18)).toMatch(/^M100,0H4Q0,0 0,4/);
  });
});

describe('StackedColumns and RangeDotChart', () => {
  it('reads every stack and the extra rows of a day', async () => {
    render(
      <StackedColumns
        label="Daily"
        summary="s"
        categories={['Jul 1', 'Jul 2']}
        stacks={[
          { id: 'r', label: 'Regular', color: 'a', values: [10, 10] },
          { id: 'o', label: 'Overtime', color: 'b', values: [0, 2] },
        ]}
        overlays={[{ id: 'd', label: 'Demand', color: 'k', values: [11, 13] }]}
        extraRows={(i) => [{ label: 'Served', value: `${i === 0 ? 10 : 12}` }]}
        formatValue={String}
        formatTick={String}
      />,
    );
    const slider = screen.getByRole('slider');
    slider.focus();
    await userEvent.keyboard('{End}');
    expect(slider.getAttribute('aria-valuetext')).toBe(
      'Jul 2. Demand: 13. Regular: 10. Overtime: 2. Served: 12',
    );
  });

  it('reads an estimate, its interval and the target', async () => {
    render(
      <RangeDotChart
        label="Coverage"
        summary="s"
        domain={[0.5, 1]}
        formatValue={(v) => `${Math.round(v * 100)} %`}
        rows={[
          {
            id: '80',
            label: '80 %',
            value: 0.8,
            low: 0.65,
            high: 0.95,
            target: 0.8,
            tolerance: 0.05,
            notes: ['ESS 8.1'],
          },
          {
            id: '95',
            label: '95 %',
            value: null,
            low: null,
            high: null,
            target: 0.95,
            tolerance: null,
          },
        ]}
      />,
    );
    const slider = screen.getByRole('slider');
    slider.focus();
    await userEvent.keyboard('{ArrowDown}');
    expect(slider.getAttribute('aria-valuetext')).toBe(
      '80 %. Observed coverage: 80 %. Confidence interval: 65 % – 95 %. Nominal level: 80 %. ESS 8.1',
    );
    await userEvent.keyboard('{ArrowDown}');
    expect(slider.getAttribute('aria-valuetext')).toContain('not computed');
  });
});

describe('helpers', () => {
  it('sizes the axis margin from its labels', () => {
    expect(leftMargin(['1K'])).toBe(32);
    expect(leftMargin(['100,000 requests'])).toBeGreaterThan(100);
  });

  it('turns a tooltip into one sentence', () => {
    expect(tooltipText({ title: 'T', rows: [{ label: 'a', value: '1' }], notes: ['n'] })).toBe(
      'T. a: 1. n',
    );
  });
});
