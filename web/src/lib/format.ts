/**
 * Display formatting only: every number shown comes from the API unchanged and is only
 * rounded here for reading (tables keep more digits than charts). Dates are calendar
 * dates (YYYY-MM-DD) and are formatted in UTC so no time zone can shift a day.
 */
const LOCALE = 'en-US';

const integer = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 });
const oneDecimal = new Intl.NumberFormat(LOCALE, {
  minimumFractionDigits: 1,
  maximumFractionDigits: 1,
});
const compact = new Intl.NumberFormat(LOCALE, { notation: 'compact', maximumFractionDigits: 1 });
const usd = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'USD',
  maximumFractionDigits: 0,
});
const usdCompact = new Intl.NumberFormat(LOCALE, {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 1,
});

export const DASH = '–';

function finite(value: number | null | undefined): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

/** Avoid "-0" after rounding. */
function clean(value: number, digits: number): number {
  const factor = 10 ** digits;
  const rounded = Math.round(value * factor) / factor;
  return Object.is(rounded, -0) ? 0 : rounded;
}

export function formatInteger(value: number | null | undefined): string {
  return finite(value) ? integer.format(clean(value, 0)) : DASH;
}

export function formatNumber(value: number | null | undefined, digits = 1): string {
  if (!finite(value)) return DASH;
  return new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(clean(value, digits));
}

export function formatSigned(value: number | null | undefined, digits = 0): string {
  if (!finite(value)) return DASH;
  const rounded = clean(value, digits);
  const text = formatNumber(Math.abs(rounded), digits);
  if (rounded > 0) return `+${text}`;
  if (rounded < 0) return `−${text}`;
  return text;
}

export function formatCompact(value: number | null | undefined): string {
  return finite(value) ? compact.format(value) : DASH;
}

export function formatUsd(value: number | null | undefined): string {
  return finite(value) ? usd.format(clean(value, 0)) : DASH;
}

export function formatUsdCompact(value: number | null | undefined): string {
  return finite(value) ? usdCompact.format(value) : DASH;
}

/** A share in [0, 1] as a percentage. */
export function formatShare(value: number | null | undefined, digits = 1): string {
  return finite(value) ? `${formatNumber(value * 100, digits)} %` : DASH;
}

/** A value already in percent. */
export function formatPct(value: number | null | undefined, digits = 1): string {
  return finite(value) ? `${formatNumber(value, digits)} %` : DASH;
}

export function formatP(value: number | null | undefined): string {
  if (!finite(value)) return DASH;
  if (value < 0.001) return '< 0.001';
  return value.toFixed(3);
}

export function formatOneDecimal(value: number | null | undefined): string {
  return finite(value) ? oneDecimal.format(value) : DASH;
}

// --- Dates ----------------------------------------------------------------------------------

const DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A calendar date (YYYY-MM-DD) as a UTC timestamp (ms). */
export function dateValue(day: string): number {
  const match = DATE.exec(day);
  if (!match) throw new Error(`not a calendar date: ${day}`);
  return Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}

export function isoDay(value: number): string {
  return new Date(value).toISOString().slice(0, 10);
}

/** Add `days` to a calendar date. */
export function addDays(day: string, days: number): string {
  return isoDay(dateValue(day) + days * 86_400_000);
}

const longDate = new Intl.DateTimeFormat(LOCALE, {
  timeZone: 'UTC',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
});
const weekdayDate = new Intl.DateTimeFormat(LOCALE, {
  timeZone: 'UTC',
  weekday: 'short',
  year: 'numeric',
  month: 'short',
  day: 'numeric',
});
const shortDate = new Intl.DateTimeFormat(LOCALE, {
  timeZone: 'UTC',
  month: 'short',
  day: 'numeric',
});
const monthYear = new Intl.DateTimeFormat(LOCALE, {
  timeZone: 'UTC',
  month: 'short',
  year: 'numeric',
});

export function formatDate(day: string): string {
  return longDate.format(dateValue(day));
}

export function formatWeekdayDate(day: string): string {
  return weekdayDate.format(dateValue(day));
}

export function formatShortDate(value: number): string {
  return shortDate.format(value);
}

export function formatMonthYear(value: number): string {
  return monthYear.format(value);
}

// --- Units and names ----------------------------------------------------------------------

const UNITS: Record<string, string> = {
  requests_per_day: 'requests/day',
  requests: 'requests',
  request_days: 'request-days',
  staff_hours: 'staff hours',
  staff_hours_per_day: 'staff hours/day',
  requests_per_staff_hour: 'requests/staff hour',
  usd: 'USD',
  usd_per_staff_hour: 'USD/staff hour',
  usd_per_request: 'USD/request',
  usd_per_request_day: 'USD-equivalent/request-day',
  ratio: '×',
  planning_day: 'planning day',
  days: 'days',
};

/** A readable unit; unknown units are shown as given (never guessed). */
export function formatUnit(unit: string): string {
  if (unit in UNITS) return UNITS[unit] ?? unit;
  if (unit.startsWith('usd_equivalent')) return 'USD-equivalent (ranking score, not a cost)';
  return unit.replace(/_/g, ' ');
}

/** `snake_case` identifiers as words, keeping their meaning (no translation). */
export function humanise(identifier: string): string {
  const text = identifier.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

const MODEL_NAMES: Record<string, string> = {
  naive: 'Naive (last value)',
  seasonal_naive: 'Seasonal naive (same weekday)',
  same_weekday_mean_4w: 'Mean of last 4 same weekdays',
  same_weekday_median_5w: 'Median of last 5 same weekdays',
  seasonal_exponential_smoothing: 'Seasonal exponential smoothing',
  legacy_linear_regression: 'Legacy linear regression',
  atlas_linear_regression: 'ATLAS linear regression',
};

/** A model's display name; the identifier itself is always shown next to it in tables. */
export function modelName(id: string): string {
  return MODEL_NAMES[id] ?? humanise(id);
}

export function shortHash(value: string | null | undefined, length = 12): string {
  return value ? value.slice(0, length) : DASH;
}
