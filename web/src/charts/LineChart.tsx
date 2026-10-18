/**
 * Lines over time (or over a numeric axis): observed history, an archived projection, a
 * band of marginal quantiles, vertical rules (the forecast origin) and point marks
 * (holidays). One y axis only. A crosshair snaps to the nearest x and lists every series.
 */
import { scaleLinear, scaleUtc } from 'd3-scale';
import { area, line } from 'd3-shape';
import { useMemo } from 'react';

import { formatMonthYear, formatShortDate } from '../lib/format';
import {
  ChartSurface,
  Legend,
  leftMargin,
  useWidth,
  type LegendItem,
  type TooltipContent,
} from './core';

export interface XY {
  readonly x: number;
  readonly y: number | null;
}

export interface LineSeries {
  readonly id: string;
  readonly label: string;
  readonly color: string;
  /** Line pattern: series are never told apart by colour alone. */
  readonly style?: 'solid' | 'dashed' | 'dotted';
  readonly points: readonly XY[];
  /** Draw a dot at every point (short series). */
  readonly markers?: boolean;
}

export interface Band {
  readonly id: string;
  readonly label: string;
  readonly fill: string;
  readonly points: readonly { x: number; low: number; high: number }[];
}

export interface Rule {
  readonly x: number;
  readonly label: string;
}

/** A horizontal reference (a HYPOTHETICAL target). */
export interface Level {
  readonly y: number;
  readonly label: string;
}

export interface Mark {
  readonly x: number;
  readonly y: number;
  readonly label: string;
}

export function LineChart({
  label,
  summary,
  series,
  bands = [],
  rules = [],
  levels = [],
  marks = [],
  marksLabel = 'Federal holiday',
  diagonal = false,
  xType = 'time',
  height = 280,
  formatValue,
  formatTick,
  formatX,
  xTickFormat,
  yDomain,
  xDomain,
  notesAt,
  stale = false,
  legendExtra = [],
}: {
  label: string;
  summary: string;
  series: readonly LineSeries[];
  bands?: readonly Band[];
  rules?: readonly Rule[];
  levels?: readonly Level[];
  marks?: readonly Mark[];
  marksLabel?: string;
  /** A y = x reference line (perfect calibration). */
  diagonal?: boolean;
  xType?: 'time' | 'linear';
  height?: number;
  formatValue: (value: number) => string;
  formatTick: (value: number) => string;
  /** The tooltip title of an x position. */
  formatX: (x: number) => string;
  xTickFormat?: (x: number) => string;
  yDomain?: readonly [number, number];
  xDomain?: readonly [number, number];
  notesAt?: (x: number) => readonly string[];
  stale?: boolean;
  legendExtra?: readonly LegendItem[];
}) {
  const [ref, width] = useWidth<HTMLDivElement>();

  const xs = useMemo(() => {
    const all = new Set<number>();
    for (const s of series) for (const p of s.points) all.add(p.x);
    for (const b of bands) for (const p of b.points) all.add(p.x);
    return [...all].sort((a, b) => a - b);
  }, [series, bands]);

  // Lookups by x for the reading (a chart can hold over a thousand days).
  const seriesAt = useMemo(
    () => series.map((s) => new Map(s.points.map((p) => [p.x, p] as const))),
    [series],
  );
  const bandsAt = useMemo(
    () => bands.map((b) => new Map(b.points.map((p) => [p.x, p] as const))),
    [bands],
  );

  const [yMin, yMax] = useMemo(() => {
    if (yDomain) return yDomain;
    let low = Infinity;
    let high = -Infinity;
    for (const s of series)
      for (const p of s.points)
        if (p.y !== null) {
          low = Math.min(low, p.y);
          high = Math.max(high, p.y);
        }
    for (const b of bands)
      for (const p of b.points) {
        low = Math.min(low, p.low);
        high = Math.max(high, p.high);
      }
    for (const level of levels) {
      low = Math.min(low, level.y);
      high = Math.max(high, level.y);
    }
    if (!Number.isFinite(low)) return [0, 1] as const;
    const pad = (high - low) * 0.06 || Math.abs(high) * 0.1 || 1;
    return [low - pad, high + pad] as const;
  }, [series, bands, levels, yDomain]);

  const y = scaleLinear().domain([yMin, yMax]).nice(5);
  const yTicks = y.ticks(5);
  const tickLabels = yTicks.map(formatTick);
  const margin = { top: 14, right: 16, bottom: 28, left: leftMargin(tickLabels) };
  const innerWidth = Math.max(40, width - margin.left - margin.right);
  const innerHeight = height - margin.top - margin.bottom;
  y.range([margin.top + innerHeight, margin.top]);

  const first = xDomain?.[0] ?? xs[0] ?? 0;
  const last = xDomain?.[1] ?? xs[xs.length - 1] ?? 1;
  const range = [margin.left, margin.left + innerWidth];
  const timeScale = scaleUtc()
    .domain([new Date(first), new Date(last)])
    .range(range);
  const linearScale = scaleLinear().domain([first, last]).range(range);
  const px = (value: number): number =>
    xType === 'time' ? timeScale(new Date(value)) : linearScale(value);
  // At least three ticks on a numeric axis: two can come back as one ([1, 30] gives 20).
  const tickCount = Math.max(xType === 'linear' ? 3 : 2, Math.min(8, Math.floor(innerWidth / 90)));
  const xTicks: number[] =
    xType === 'time'
      ? timeScale.ticks(tickCount).map((d) => d.getTime())
      : linearScale.ticks(tickCount);
  const tickText =
    xTickFormat ?? (xType === 'time' ? defaultTimeTick(first, last) : (v: number) => String(v));

  // Dots only where the line still shows between them: packed closer they would cover the
  // dash patterns that tell the series apart (a single series has none to keep).
  const pointGap = innerWidth / Math.max(1, xs.length - 1);
  const showMarkers = series.length === 1 || pointGap >= 20;

  // A rule's label goes on the side of its line with room for it (11 px text: about 7 px a
  // character, which leaves room for user text spacing).
  const flipRule = (rule: Rule): boolean => {
    const right = width - px(rule.x) - 6;
    return right < rule.label.length * 7 && px(rule.x) - 6 > right;
  };

  const pathOf = (points: readonly XY[]) =>
    line<XY>()
      .defined((p) => p.y !== null)
      .x((p) => px(p.x))
      .y((p) => y(p.y ?? 0))(points as XY[]) ?? '';
  const linePaths = series.map((s) => pathOf(s.points));
  const bandPath = (b: Band) =>
    area<{ x: number; low: number; high: number }>()
      .x((p) => px(p.x))
      .y0((p) => y(p.low))
      .y1((p) => y(p.high))(b.points as { x: number; low: number; high: number }[]) ?? '';

  const contentAt = (index: number): TooltipContent => {
    const at = xs[index] ?? 0;
    const rows = [];
    for (const [i, s] of series.entries()) {
      const point = seriesAt[i]?.get(at);
      if (point && point.y !== null)
        rows.push({
          label: s.label,
          value: formatValue(point.y),
          color: s.color,
          style: s.style ?? 'solid',
        });
      else if (point && point.y === null)
        rows.push({ label: s.label, value: 'not recorded', color: s.color });
    }
    for (const [i, b] of bands.entries()) {
      const point = bandsAt[i]?.get(at);
      if (point)
        rows.push({
          label: b.label,
          value: `${formatValue(point.low)} – ${formatValue(point.high)}`,
          color: b.fill,
          style: 'swatch' as const,
        });
    }
    const notes = [
      ...(notesAt?.(at) ?? []),
      ...marks.filter((m) => m.x === at).map((m) => `${marksLabel}: ${m.label}`),
    ];
    return { title: formatX(at), rows, notes };
  };

  // Pixel positions once per render; a pointer move is then a binary search.
  const positions = xs.map(px);
  const indexAtPointer = (pointerX: number) => {
    if (positions.length === 0) return null;
    let low = 0;
    let high = positions.length - 1;
    while (low < high) {
      const middle = (low + high) >> 1;
      if ((positions[middle] ?? 0) < pointerX) low = middle + 1;
      else high = middle;
    }
    const before = Math.max(0, low - 1);
    return Math.abs((positions[before] ?? 0) - pointerX) <=
      Math.abs((positions[low] ?? 0) - pointerX)
      ? before
      : low;
  };

  const legend: LegendItem[] = [
    ...series.map((s) => ({
      label: s.label,
      color: s.color,
      kind: s.style === 'dashed' || s.style === 'dotted' ? s.style : ('line' as const),
    })),
    ...bands.map((b) => ({ label: b.label, color: b.fill, kind: 'swatch' as const })),
    ...(marks.length > 0
      ? [{ label: marksLabel, color: 'currentColor', kind: 'diamond' as const }]
      : []),
    ...legendExtra,
  ];

  // Everything but the reading, built once per render of the chart: moving the pointer
  // re-renders only the crosshair (React skips an unchanged element).
  const layers = (
    <>
      <g className="axis">
        {yTicks.map((tick, i) => (
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
        <line
          className="axis-baseline"
          x1={margin.left}
          x2={margin.left + innerWidth}
          y1={margin.top + innerHeight}
          y2={margin.top + innerHeight}
        />
        {xTicks.map((tick) => (
          <text key={tick} x={px(tick)} y={margin.top + innerHeight + 18} textAnchor="middle">
            {tickText(tick)}
          </text>
        ))}
      </g>
      {bands.map((b) => (
        <path key={b.id} d={bandPath(b)} style={{ fill: b.fill, stroke: 'none' }} />
      ))}
      {diagonal && (
        <line
          className="rule-line"
          x1={px(first)}
          y1={y(first)}
          x2={px(last)}
          y2={y(last)}
          strokeDasharray="2 3"
        />
      )}
      {rules.map((rule) => (
        <g key={rule.label}>
          <line
            className="rule-line"
            x1={px(rule.x)}
            x2={px(rule.x)}
            y1={margin.top}
            y2={margin.top + innerHeight}
          />
          <text
            className="rule-label"
            x={px(rule.x) + (flipRule(rule) ? -6 : 6)}
            y={margin.top + 10}
            textAnchor={flipRule(rule) ? 'end' : 'start'}
          >
            {rule.label}
          </text>
        </g>
      ))}
      {levels.map((level) => (
        <g key={level.label}>
          <line
            className="rule-line"
            x1={margin.left}
            x2={margin.left + innerWidth}
            y1={y(level.y)}
            y2={y(level.y)}
            strokeDasharray="2 3"
          />
          <text
            className="rule-label halo"
            x={margin.left + innerWidth - 4}
            y={y(level.y) - 6}
            textAnchor="end"
          >
            {level.label}
          </text>
        </g>
      ))}
      {/* Over a band, each line runs on a surface-coloured halo: it is then seen against the
          surface (at least 3:1), not against its own band's tint (as low as 2.2:1). */}
      {bands.length > 0 &&
        series.map((s, i) => (
          <path key={`${s.id}-halo`} className="series-halo" d={linePaths[i]} />
        ))}
      {series.map((s, i) => (
        <g key={s.id}>
          <path
            className="series-line"
            d={linePaths[i]}
            style={{ stroke: s.color }}
            data-style={s.style ?? 'solid'}
          />
          {s.markers === true &&
            showMarkers &&
            s.points.map((p) =>
              p.y === null ? null : (
                <circle
                  key={p.x}
                  className="marker"
                  cx={px(p.x)}
                  cy={y(p.y)}
                  r={4}
                  style={{ fill: s.color }}
                />
              ),
            )}
        </g>
      ))}
      {marks.map((m) => (
        <rect
          key={`${m.x}:${m.label}`}
          className="holiday-mark"
          x={px(m.x) - 4}
          y={y(m.y) - 4}
          width={8}
          height={8}
          transform={`rotate(45 ${px(m.x)} ${y(m.y)})`}
        />
      ))}
    </>
  );

  return (
    <div ref={ref} className="stack-2">
      {legend.length > 1 ? <Legend items={legend} /> : null}
      <ChartSurface
        label={label}
        summary={summary}
        count={xs.length}
        width={width}
        height={height}
        contentAt={contentAt}
        anchorAt={(index) => ({ x: px(xs[index] ?? 0), y: margin.top })}
        indexAtPointer={indexAtPointer}
        stale={stale}
      >
        {(active) => (
          <>
            {layers}
            {active !== null && xs[active] !== undefined && (
              <g>
                <line
                  className="crosshair"
                  x1={px(xs[active])}
                  x2={px(xs[active])}
                  y1={margin.top}
                  y2={margin.top + innerHeight}
                />
                {series.map((s, i) => {
                  const point = seriesAt[i]?.get(xs[active] ?? NaN);
                  return point && point.y !== null ? (
                    <circle
                      key={s.id}
                      className="marker"
                      cx={px(point.x)}
                      cy={y(point.y)}
                      r={4.5}
                      style={{ fill: s.color }}
                    />
                  ) : null;
                })}
              </g>
            )}
          </>
        )}
      </ChartSurface>
    </div>
  );
}

function defaultTimeTick(first: number, last: number): (value: number) => string {
  const spanDays = (last - first) / 86_400_000;
  return spanDays > 120 ? formatMonthYear : formatShortDate;
}
