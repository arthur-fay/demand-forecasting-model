/**
 * Daily stacked columns (what serves each day: regular capacity, overtime, surge) with
 * an optional line on the same axis and unit (demand). Columns are separated by a 2 px
 * surface gap; segments by a 2 px gap too.
 */
import { scaleBand, scaleLinear } from 'd3-scale';
import { line } from 'd3-shape';

import {
  ChartSurface,
  Legend,
  leftMargin,
  useWidth,
  type LegendItem,
  type TooltipContent,
  type TooltipRow,
} from './core';

export interface Stack {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  readonly values: readonly number[];
}

export interface Overlay {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  readonly values: readonly number[];
  readonly style?: 'solid' | 'dashed';
}

export function StackedColumns({
  label,
  summary,
  categories,
  stacks,
  overlays = [],
  formatValue,
  formatTick,
  height = 260,
  extraRows,
}: {
  label: string;
  summary: string;
  /** One label per column (dates), used by the tooltip and the axis. */
  categories: readonly string[];
  stacks: readonly Stack[];
  overlays?: readonly Overlay[];
  formatValue: (value: number) => string;
  formatTick: (value: number) => string;
  height?: number;
  /** More tooltip rows for a column (values from the API, e.g. what was served). */
  extraRows?: (index: number) => readonly TooltipRow[];
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const totals = categories.map((_, i) =>
    stacks.reduce((sum, s) => sum + Math.max(0, s.values[i] ?? 0), 0),
  );
  const peak = Math.max(1, ...totals, ...overlays.flatMap((o) => o.values));
  const y = scaleLinear().domain([0, peak]).nice(5);
  const ticks = y.ticks(5);
  const tickLabels = ticks.map(formatTick);
  const margin = { top: 12, right: 12, bottom: 28, left: leftMargin(tickLabels) };
  const innerWidth = Math.max(40, width - margin.left - margin.right);
  const innerHeight = height - margin.top - margin.bottom;
  y.range([margin.top + innerHeight, margin.top]);
  const x = scaleBand<number>()
    .domain(categories.map((_, i) => i))
    .range([margin.left, margin.left + innerWidth])
    .paddingInner(0)
    .paddingOuter(0);
  const step = x.bandwidth();
  const barWidth = Math.max(2, Math.min(24, step - 2));
  const offset = (step - barWidth) / 2;
  const every = Math.max(
    1,
    Math.ceil(categories.length / Math.max(2, Math.floor(innerWidth / 70))),
  );

  const contentAt = (index: number): TooltipContent => ({
    title: categories[index] ?? '',
    rows: [
      ...overlays.map((o) => ({
        label: o.label,
        value: formatValue(o.values[index] ?? 0),
        color: o.color,
        style: o.style ?? ('solid' as const),
      })),
      ...stacks.map((s) => ({
        label: s.label,
        value: formatValue(s.values[index] ?? 0),
        color: s.color,
        style: 'swatch' as const,
      })),
      ...(extraRows?.(index) ?? []),
    ],
  });

  const legend: LegendItem[] = [
    ...stacks.map((s) => ({ label: s.label, color: s.color, kind: 'swatch' as const })),
    ...overlays.map((o) => ({
      label: o.label,
      color: o.color,
      kind: o.style === 'dashed' ? ('dashed' as const) : ('line' as const),
    })),
  ];

  return (
    <div ref={ref} className="stack-2">
      <Legend items={legend} />
      <ChartSurface
        label={label}
        summary={summary}
        count={categories.length}
        width={width}
        height={height}
        contentAt={contentAt}
        anchorAt={(index) => ({ x: (x(index) ?? 0) + step / 2, y: margin.top })}
        indexAtPointer={(pointerX) => {
          const index = Math.floor((pointerX - margin.left) / step);
          return index >= 0 && index < categories.length ? index : null;
        }}
      >
        {(active) => (
          <>
            <g className="axis">
              {ticks.map((tick, i) => (
                <g key={tick}>
                  <line
                    className="grid-line"
                    x1={margin.left}
                    x2={margin.left + innerWidth}
                    y1={y(tick)}
                    y2={y(tick)}
                  />
                  <text x={margin.left - 8} y={y(tick)} dy="0.32em" textAnchor="end">
                    {tickLabels[i]}
                  </text>
                </g>
              ))}
              {categories.map((category, i) =>
                i % every === 0 ? (
                  <text
                    key={category}
                    x={(x(i) ?? 0) + step / 2}
                    y={margin.top + innerHeight + 18}
                    textAnchor="middle"
                  >
                    {category}
                  </text>
                ) : null,
              )}
            </g>
            {categories.map((category, i) => {
              let base = 0;
              const left = (x(i) ?? 0) + offset;
              return (
                <g key={category} opacity={active === null || active === i ? 1 : 0.6}>
                  {stacks.map((s) => {
                    const value = Math.max(0, s.values[i] ?? 0);
                    const bottom = y(base);
                    base += value;
                    const top = y(base);
                    const h = bottom - top - (value > 0 ? 2 : 0);
                    return h > 0 ? (
                      <rect
                        key={s.id}
                        x={left}
                        y={top}
                        width={barWidth}
                        height={h}
                        style={{ fill: s.color }}
                      />
                    ) : null;
                  })}
                </g>
              );
            })}
            <line
              className="axis-baseline"
              x1={margin.left}
              x2={margin.left + innerWidth}
              y1={margin.top + innerHeight}
              y2={margin.top + innerHeight}
            />
            {overlays.map((o) => (
              <path
                key={o.id}
                className="series-line"
                data-style={o.style ?? 'solid'}
                style={{ stroke: o.color }}
                d={
                  line<number>()
                    .x((_, i) => (x(i) ?? 0) + step / 2)
                    .y((v) => y(v))(o.values as number[]) ?? ''
                }
              />
            ))}
            {active !== null && (
              <line
                className="crosshair"
                x1={(x(active) ?? 0) + step / 2}
                x2={(x(active) ?? 0) + step / 2}
                y1={margin.top}
                y2={margin.top + innerHeight}
              />
            )}
          </>
        )}
      </ChartSurface>
    </div>
  );
}
