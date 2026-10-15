/** Turn API points into chart series. Values are passed through unchanged. */
import type { LineSeries, Mark } from '../charts/LineChart';
import type { ForecastPoint, ObservedPoint } from '../api/schemas';
import { dateValue } from '../lib/format';

export const OBSERVED_COLOR = 'var(--series-1)';
export const PROJECTED_COLOR = 'var(--series-2)';

export function observedSeries(points: readonly ObservedPoint[]): LineSeries {
  return {
    id: 'observed',
    label: 'Observed requests/day',
    color: OBSERVED_COLOR,
    points: points.map((p) => ({ x: dateValue(p.date), y: p.requests })),
  };
}

export function projectedSeries(
  points: readonly ForecastPoint[],
  label = 'Archived projection (M2 point forecast)',
): LineSeries {
  return {
    id: 'projected',
    label,
    color: PROJECTED_COLOR,
    style: 'dashed',
    points: points.map((p) => ({ x: dateValue(p.target_date), y: p.prediction })),
  };
}

/** Federal holidays (M1 rules) on either series, marked where they fall. */
export function holidayMarks(
  observed: readonly ObservedPoint[],
  projected: readonly ForecastPoint[],
): Mark[] {
  const marks: Mark[] = [];
  for (const p of observed)
    if (p.federal_holiday !== null && p.requests !== null)
      marks.push({ x: dateValue(p.date), y: p.requests, label: p.federal_holiday });
  for (const p of projected)
    if (p.federal_holiday !== null)
      marks.push({ x: dateValue(p.target_date), y: p.prediction, label: p.federal_holiday });
  return marks;
}
