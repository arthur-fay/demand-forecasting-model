/**
 * Shared chart machinery: measured width, the focusable interactive surface (pointer and
 * keyboard read the same values), the tooltip, the live region and the legend.
 *
 * Charts are SVG drawn by React (scales from d3-scale, paths from d3-shape): no DOM
 * library, no inline style attributes, no HTML strings. Text from the API is only ever
 * rendered as React text. Every chart has a table twin in its panel, so the tooltip
 * enhances but never gates a value.
 */
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';

export const FALLBACK_WIDTH = 640;

/** The width of an element, followed with a ResizeObserver. */
export function useWidth<T extends HTMLElement>(): [React.RefObject<T | null>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(FALLBACK_WIDTH);
  useEffect(() => {
    const element = ref.current;
    if (element === null || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver((entries) => {
      const measured = entries[0]?.contentRect.width;
      if (measured !== undefined && measured > 0) setWidth(Math.round(measured));
    });
    observer.observe(element);
    return () => {
      observer.disconnect();
    };
  }, []);
  return [ref, width];
}

export interface LegendItem {
  readonly label: string;
  readonly color: string;
  readonly kind: 'line' | 'dashed' | 'dotted' | 'swatch' | 'diamond';
}

export function Legend({ items }: { items: readonly LegendItem[] }) {
  if (items.length === 0) return null;
  return (
    <ul className="chart-legend" aria-label="Legend">
      {items.map((item) => (
        <li key={item.label}>
          <LegendKey item={item} />
          {item.label}
        </li>
      ))}
    </ul>
  );
}

function LegendKey({ item }: { item: LegendItem }) {
  if (item.kind === 'swatch') {
    return (
      <svg width="12" height="10" aria-hidden="true">
        <rect width="12" height="10" rx="2" style={{ fill: item.color }} />
      </svg>
    );
  }
  if (item.kind === 'diamond') return <span className="legend-diamond" aria-hidden="true" />;
  return (
    <svg width="18" height="4" aria-hidden="true">
      <line
        x1="0"
        x2="18"
        y1="2"
        y2="2"
        style={{ stroke: item.color }}
        strokeWidth="2"
        strokeDasharray={DASHES[item.kind]}
      />
    </svg>
  );
}

/** The dash pattern of each line style (the chart CSS draws the same patterns). */
const DASHES: Readonly<Record<string, string | undefined>> = { dashed: '5 4', dotted: '1.5 3.5' };

export interface TooltipRow {
  readonly label: string;
  readonly value: string;
  readonly color?: string;
  readonly style?: 'solid' | 'dashed' | 'dotted' | 'swatch';
}

export interface TooltipContent {
  readonly title: string;
  readonly rows: readonly TooltipRow[];
  readonly notes?: readonly string[];
}

/** The text a screen reader hears for a tooltip. */
export function tooltipText(content: TooltipContent): string {
  const rows = content.rows.map((row) => `${row.label}: ${row.value}`);
  return [content.title, ...rows, ...(content.notes ?? [])].join('. ');
}

function Tooltip({
  content,
  x,
  y,
  width,
}: {
  content: TooltipContent;
  x: number;
  y: number;
  width: number;
}) {
  // Right of the reading if it fits, else left of it; always inside the chart (a phone's
  // chart can be narrower than the tooltip's usual width).
  const ref = useRef<HTMLDivElement>(null);
  const [measured, setMeasured] = useState(220);
  useLayoutEffect(() => {
    const own = ref.current?.offsetWidth;
    if (own !== undefined && own > 0) setMeasured(own);
  }, [content, width]);
  const preferred = x + 14 + measured <= width ? x + 14 : x - 14 - measured;
  const left = Math.max(0, Math.min(width - measured, preferred));
  return (
    <div
      ref={ref}
      className="tooltip"
      aria-hidden="true"
      style={{ left, top: Math.max(0, y), maxWidth: width }}
    >
      <span className="tooltip-title">{content.title}</span>
      {content.rows.map((row) => (
        <span className="tooltip-row" key={row.label}>
          {row.style === 'swatch' ? (
            <svg width="12" height="10" aria-hidden="true">
              <rect width="12" height="10" rx="2" style={{ fill: row.color ?? 'currentColor' }} />
            </svg>
          ) : (
            <svg width="14" height="4" aria-hidden="true">
              <line
                x1="0"
                x2="14"
                y1="2"
                y2="2"
                style={{ stroke: row.color ?? 'currentColor' }}
                strokeWidth="2"
                strokeDasharray={row.style === undefined ? undefined : DASHES[row.style]}
              />
            </svg>
          )}
          <span className="value">{row.value}</span>
          <span className="name">{row.label}</span>
        </span>
      ))}
      {(content.notes ?? []).map((note) => (
        <span className="tooltip-note" key={note}>
          {note}
        </span>
      ))}
    </div>
  );
}

/**
 * The interactive surface of a chart. It owns the active index (pointer or keyboard),
 * shows the tooltip for it and announces it. `count` is the number of positions the
 * reader can step through; `contentAt(i)` describes position i; `anchorAt(i)` gives the
 * tooltip's pixel anchor.
 */
export function ChartSurface({
  label,
  summary,
  count,
  width,
  height,
  contentAt,
  anchorAt,
  indexAtPointer,
  children,
  stale = false,
  keys = 'horizontal',
}: {
  label: string;
  summary: string;
  count: number;
  width: number;
  height: number;
  contentAt: (index: number) => TooltipContent;
  anchorAt: (index: number) => { x: number; y: number };
  indexAtPointer: (x: number, y: number) => number | null;
  children: (active: number | null) => React.ReactNode;
  stale?: boolean;
  keys?: 'horizontal' | 'vertical';
}) {
  // A reading belongs to the data it was taken on: when new data has another number of
  // positions it is dropped, never carried over (its index would name another day, or one
  // past the new end).
  const [reading, setReading] = useState<{ index: number; count: number } | null>(null);
  const active = reading?.count === count ? reading.index : null;
  const svgRef = useRef<SVGSVGElement>(null);
  const summaryId = useId();

  const select = useCallback(
    (index: number | null) => {
      if (index === null || count === 0) {
        setReading(null);
        return;
      }
      setReading({ index: Math.max(0, Math.min(count - 1, index)), count });
    },
    [count],
  );

  const pointer = (event: React.PointerEvent<SVGSVGElement>) => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return;
    const scale = box.width > 0 ? width / box.width : 1;
    select(indexAtPointer((event.clientX - box.left) * scale, (event.clientY - box.top) * scale));
  };

  const keydown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    const next = keys === 'horizontal' ? 'ArrowRight' : 'ArrowDown';
    const previous = keys === 'horizontal' ? 'ArrowLeft' : 'ArrowUp';
    let index: number | null = null;
    if (event.key === next) index = active === null ? 0 : active + 1;
    else if (event.key === previous) index = active === null ? count - 1 : active - 1;
    else if (event.key === 'Home') index = 0;
    else if (event.key === 'End') index = count - 1;
    else if (event.key === 'Escape') {
      setReading(null);
      return;
    } else return;
    event.preventDefault();
    select(index);
  };

  const anchor = active === null ? null : anchorAt(active);
  const content = active === null ? null : contentAt(active);
  const hint = keys === 'horizontal' ? 'left and right' : 'up and down';
  return (
    <div className="chart" data-stale={stale ? 'true' : undefined}>
      {/* A slider over the chart's positions: arrow keys move it, and assistive technology
          reads the position's values (aria-valuetext); the table view lists them all. */}
      <div
        className="chart-surface"
        role="slider"
        tabIndex={0}
        aria-label={`${label}. Use the ${hint} arrow keys to read the values; the table view lists every value.`}
        aria-describedby={summaryId}
        aria-orientation={keys === 'horizontal' ? 'horizontal' : 'vertical'}
        aria-valuemin={0}
        aria-valuemax={count}
        aria-valuenow={active === null ? 0 : active + 1}
        aria-valuetext={content === null ? 'No value selected' : tooltipText(content)}
        onKeyDown={keydown}
        onBlur={() => {
          setReading(null);
        }}
      >
        <svg
          ref={svgRef}
          className="chart-svg"
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          aria-hidden="true"
          onPointerMove={pointer}
          onPointerDown={pointer}
          onPointerLeave={(event) => {
            // A finger lifts and "leaves" at once: keep its reading until the next touch.
            if (event.pointerType !== 'touch') setReading(null);
          }}
        >
          {children(active)}
        </svg>
        {content !== null && anchor !== null && (
          <Tooltip content={content} x={anchor.x} y={anchor.y} width={width} />
        )}
      </div>
      <p id={summaryId} className="visually-hidden">
        {summary}
      </p>
    </div>
  );
}

/** Where a y tick label fits: the widest label in characters, at about 6.6 px each. */
export function leftMargin(labels: readonly string[]): number {
  const longest = labels.reduce((max, text) => Math.max(max, text.length), 0);
  return Math.max(32, Math.ceil(longest * 6.6) + 12);
}
