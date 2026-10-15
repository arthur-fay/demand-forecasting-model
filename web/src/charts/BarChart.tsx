/**
 * Horizontal bars (long category names stay readable on a phone). Bars grow from one
 * baseline (zero), have a 4 px rounded data end, are at most 20 px thick, and carry
 * their value at the tip. Negative values grow left of the baseline.
 */
import { scaleBand, scaleLinear } from 'd3-scale';

import { ChartSurface, Legend, useWidth, type LegendItem, type TooltipContent } from './core';

export interface BarRow {
  readonly id: string;
  readonly label: string;
  readonly value: number | null;
  readonly color: string;
  /** Extra lines for the tooltip (a status, a caveat). */
  readonly notes?: readonly string[];
  /** A short text shown after the value (e.g. "DIAGNOSTIC"). */
  readonly suffix?: string;
}

const ROW = 34;
const BAR = 18;

/** A bar with a 4 px rounded end away from the baseline, square at the baseline. */
export function barPath(x0: number, x1: number, top: number, height: number): string {
  const r = Math.min(4, Math.abs(x1 - x0), height / 2);
  if (x1 >= x0) {
    return `M${x0},${top}H${x1 - r}Q${x1},${top} ${x1},${top + r}V${top + height - r}Q${x1},${top + height} ${x1 - r},${top + height}H${x0}Z`;
  }
  return `M${x0},${top}H${x1 + r}Q${x1},${top} ${x1},${top + r}V${top + height - r}Q${x1},${top + height} ${x1 + r},${top + height}H${x0}Z`;
}

export function BarChart({
  label,
  summary,
  rows,
  formatValue,
  valueLabel,
  legend = [],
  labelWidth: requestedLabelWidth,
}: {
  label: string;
  summary: string;
  rows: readonly BarRow[];
  formatValue: (value: number) => string;
  /** What the value is, with its unit (tooltip). */
  valueLabel: string;
  legend?: readonly LegendItem[];
  labelWidth?: number;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const narrow = width < 520;
  const labelWidth = narrow ? 0 : (requestedLabelWidth ?? Math.min(240, Math.round(width * 0.34)));
  const labelBand = narrow ? 16 : 0;
  const rowHeight = ROW + labelBand;
  const height = rows.length * rowHeight + 8;
  const values = rows.map((r) => r.value ?? 0);
  const low = Math.min(0, ...values);
  const high = Math.max(0, ...values);
  // Room for the value text at the bar's tip, measured from the longest label (about 7 px a
  // character), so a label never overflows the chart. On a narrow screen the suffix moves to
  // the category line.
  const valueText = (row: BarRow) =>
    `${row.value === null ? 'not reported' : formatValue(row.value)}${row.suffix && !narrow ? ` · ${row.suffix}` : ''}`;
  const longest = rows.reduce((max, row) => Math.max(max, valueText(row).length), 0);
  const valueRoom = Math.min(Math.round(width * 0.45), longest * 7 + 14);
  const x = scaleLinear()
    .domain([low, high === low ? low + 1 : high])
    .range([labelWidth + (low < 0 ? valueRoom : 4), width - valueRoom]);
  const band = scaleBand()
    .domain(rows.map((r) => r.id))
    .range([0, rows.length * rowHeight]);

  const contentAt = (index: number): TooltipContent => {
    const row = rows[index];
    if (!row) return { title: '', rows: [] };
    return {
      title: row.label,
      rows: [
        {
          label: valueLabel,
          value:
            row.value === null
              ? 'not reported'
              : `${formatValue(row.value)}${row.suffix ? ` (${row.suffix})` : ''}`,
          color: row.color,
          style: 'swatch',
        },
      ],
      notes: row.notes ?? [],
    };
  };

  return (
    <div ref={ref} className="stack-2">
      <Legend items={legend} />
      <ChartSurface
        label={label}
        summary={summary}
        count={rows.length}
        width={width}
        height={height}
        keys="vertical"
        contentAt={contentAt}
        anchorAt={(index) => ({
          x: Math.min(width - 40, x(Math.max(0, rows[index]?.value ?? 0))),
          y: (band(rows[index]?.id ?? '') ?? 0) + rowHeight,
        })}
        indexAtPointer={(_, pointerY) => {
          const index = Math.floor(pointerY / rowHeight);
          return index >= 0 && index < rows.length ? index : null;
        }}
      >
        {(active) => (
          <>
            {rows.map((row, index) => {
              const top = (band(row.id) ?? 0) + labelBand + (ROW - BAR) / 2;
              const zero = x(0);
              const end = row.value === null ? zero : x(row.value);
              const negative = (row.value ?? 0) < 0;
              return (
                <g key={row.id} opacity={active === null || active === index ? 1 : 0.55}>
                  {narrow ? (
                    <text className="bar-category" x={4} y={(band(row.id) ?? 0) + 12}>
                      {row.label}
                      {row.suffix ? ` · ${row.suffix}` : ''}
                    </text>
                  ) : (
                    <text
                      className="bar-category"
                      x={labelWidth - 8}
                      y={top + BAR / 2}
                      dy="0.32em"
                      textAnchor="end"
                    >
                      {row.label}
                    </text>
                  )}
                  {row.value !== null && (
                    <path d={barPath(zero, end, top, BAR)} style={{ fill: row.color }} />
                  )}
                  <text
                    className="bar-label"
                    x={negative ? end - 6 : end + 6}
                    y={top + BAR / 2}
                    dy="0.32em"
                    textAnchor={negative ? 'end' : 'start'}
                  >
                    {valueText(row)}
                  </text>
                </g>
              );
            })}
            {narrow ? (
              // On a phone the category sits above its bar: the zero line spans the bars
              // only, so it never runs through a label.
              rows.map((row) => {
                const top = (band(row.id) ?? 0) + labelBand + (ROW - BAR) / 2;
                return (
                  <line
                    key={`zero:${row.id}`}
                    className="axis-baseline"
                    x1={x(0)}
                    x2={x(0)}
                    y1={top - 3}
                    y2={top + BAR + 3}
                  />
                );
              })
            ) : (
              <line className="axis-baseline" x1={x(0)} x2={x(0)} y1={0} y2={height - 8} />
            )}
          </>
        )}
      </ChartSurface>
    </div>
  );
}
