/**
 * Estimates with intervals against a target (empirical interval coverage against its
 * nominal level): a dot for the estimate, a whisker for its confidence interval, a tick
 * for the target and a band for the pre-registered tolerance.
 */
import { scaleLinear } from 'd3-scale';

import { ChartSurface, Legend, useWidth, type TooltipContent } from './core';

export interface RangeRow {
  readonly id: string;
  readonly label: string;
  readonly value: number | null;
  readonly low: number | null;
  readonly high: number | null;
  readonly target: number;
  readonly tolerance: number | null;
  readonly notes?: readonly string[];
}

const ROW = 56;

export function RangeDotChart({
  label,
  summary,
  rows,
  domain,
  formatValue,
}: {
  label: string;
  summary: string;
  rows: readonly RangeRow[];
  domain: readonly [number, number];
  formatValue: (value: number) => string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const labelWidth = width < 520 ? 64 : 120;
  const height = rows.length * ROW + 28;
  const x = scaleLinear()
    .domain([domain[0], domain[1]])
    .range([labelWidth, width - 16]);
  const ticks = x.ticks(Math.max(3, Math.floor((width - labelWidth) / 80)));

  const contentAt = (index: number): TooltipContent => {
    const row = rows[index];
    if (!row) return { title: '', rows: [] };
    return {
      title: row.label,
      rows: [
        {
          label: 'Observed coverage',
          value: row.value === null ? 'not computed' : formatValue(row.value),
          color: 'var(--series-1)',
        },
        {
          label: 'Confidence interval',
          value:
            row.low === null || row.high === null
              ? 'not computed'
              : `${formatValue(row.low)} – ${formatValue(row.high)}`,
          color: 'var(--series-1)',
        },
        { label: 'Nominal level', value: formatValue(row.target), color: 'var(--ink-secondary)' },
      ],
      notes: row.notes ?? [],
    };
  };

  return (
    <div ref={ref} className="stack-2">
      <Legend
        items={[
          {
            label: 'Observed coverage (dot) and its confidence interval',
            color: 'var(--series-1)',
            kind: 'line',
          },
          {
            label: 'Pre-registered tolerance around the nominal level',
            color: 'var(--series-2-wash-strong)',
            kind: 'swatch',
          },
        ]}
      />
      <ChartSurface
        label={label}
        summary={summary}
        count={rows.length}
        width={width}
        height={height}
        keys="vertical"
        contentAt={contentAt}
        anchorAt={(index) => ({ x: x(rows[index]?.value ?? domain[0]), y: index * ROW + ROW })}
        indexAtPointer={(_, pointerY) => {
          const index = Math.floor(pointerY / ROW);
          return index >= 0 && index < rows.length ? index : null;
        }}
      >
        {(active) => (
          <>
            <g className="axis">
              {ticks.map((tick) => (
                <g key={tick}>
                  <line
                    className="grid-line"
                    x1={x(tick)}
                    x2={x(tick)}
                    y1={4}
                    y2={rows.length * ROW}
                  />
                  <text x={x(tick)} y={rows.length * ROW + 18} textAnchor="middle">
                    {formatValue(tick)}
                  </text>
                </g>
              ))}
            </g>
            {rows.map((row, index) => {
              const mid = index * ROW + ROW / 2;
              return (
                <g key={row.id} opacity={active === null || active === index ? 1 : 0.6}>
                  <text
                    className="bar-category"
                    x={labelWidth - 10}
                    y={mid}
                    dy="0.32em"
                    textAnchor="end"
                  >
                    {row.label}
                  </text>
                  {row.tolerance !== null && (
                    <rect
                      x={x(row.target - row.tolerance)}
                      width={Math.max(
                        1,
                        x(row.target + row.tolerance) - x(row.target - row.tolerance),
                      )}
                      y={mid - 14}
                      height={28}
                      style={{ fill: 'var(--series-2-wash-strong)' }}
                      rx={3}
                    />
                  )}
                  <line
                    className="rule-line"
                    x1={x(row.target)}
                    x2={x(row.target)}
                    y1={mid - 16}
                    y2={mid + 16}
                  />
                  {row.low !== null && row.high !== null && (
                    <g style={{ stroke: 'var(--series-1)' }} strokeWidth={2}>
                      <line x1={x(row.low)} x2={x(row.high)} y1={mid} y2={mid} />
                      <line x1={x(row.low)} x2={x(row.low)} y1={mid - 6} y2={mid + 6} />
                      <line x1={x(row.high)} x2={x(row.high)} y1={mid - 6} y2={mid + 6} />
                    </g>
                  )}
                  {row.value !== null && (
                    <circle
                      className="marker"
                      cx={x(row.value)}
                      cy={mid}
                      r={6}
                      style={{ fill: 'var(--series-1)' }}
                    />
                  )}
                </g>
              );
            })}
          </>
        )}
      </ChartSurface>
    </div>
  );
}
