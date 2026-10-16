/**
 * The simulator's form model: text inputs as typed, validated against the ranges the API
 * publishes (GET /api/v1/simulations/parameters), turned into a request that carries only
 * what the user changed (omitted fields take the reference defaults, as the API defines).
 *
 * Client checks give immediate feedback only. The API validates everything again (M4's own
 * parser, cross-field rules included) and its field errors are mapped back onto the form.
 */
import type { ParameterSpec, SimulationParametersData, SimulationRequest } from '../api/schemas';

export interface FieldState {
  readonly text: string;
  /** For limits the API lets a client remove (`nullable`): no limit at all. */
  readonly unlimited: boolean;
}

export interface FormState {
  readonly horizon: string;
  readonly basis: 'point_forecast' | 'm3_marginal_quantile';
  readonly quantile: string;
  readonly multiplier: string;
  readonly shift: string;
  readonly parameters: Readonly<Record<string, FieldState>>;
}

/** Field keys: `horizon_days`, `demand.multiplier`, `demand.shift_per_day`, `parameters.<name>`. */
export type FieldErrors = Readonly<Record<string, string>>;

export const INTEGER_PARAMETERS = new Set(['surge_available_from_day']);

export function initialForm(spec: SimulationParametersData): FormState {
  const parameters: Record<string, FieldState> = {};
  for (const p of spec.parameters) {
    parameters[p.name] = {
      text: p.default === null ? '' : String(p.default),
      unlimited: p.default === null,
    };
  }
  return {
    horizon: String(spec.horizon_days.default),
    basis: 'point_forecast',
    quantile: String(spec.demand.quantile[1] ?? spec.demand.quantile[0] ?? ''),
    multiplier: String(spec.demand.multiplier.default),
    shift: String(spec.demand.shift_per_day.default),
    parameters,
  };
}

const NUMBER = /^-?(\d+(\.\d*)?|\.\d+)([eE][-+]?\d+)?$/;

/** A finite number from what was typed, or a reason it is not one. */
export function parseNumber(text: string): number | string {
  const trimmed = text.trim();
  if (trimmed === '') return 'Enter a value.';
  if (!NUMBER.test(trimmed)) return 'Enter a number (digits, optionally a decimal point).';
  const value = Number(trimmed);
  return Number.isFinite(value) ? value : 'Enter a finite number.';
}

function checkRange(
  value: number | string,
  minimum: number,
  maximum: number,
  integer: boolean,
  unit: string,
): string | null {
  if (typeof value === 'string') return value;
  if (integer && !Number.isInteger(value)) return 'Enter a whole number.';
  if (value < minimum || value > maximum)
    return `Must be between ${minimum.toLocaleString('en-US')} and ${maximum.toLocaleString('en-US')} ${unit}.`;
  return null;
}

export function validate(form: FormState, spec: SimulationParametersData): FieldErrors {
  const errors: Record<string, string> = {};
  const horizon = checkRange(
    parseNumber(form.horizon),
    spec.horizon_days.minimum,
    spec.horizon_days.maximum,
    true,
    'days',
  );
  if (horizon !== null) errors.horizon_days = horizon;
  const multiplier = checkRange(
    parseNumber(form.multiplier),
    spec.demand.multiplier.minimum,
    spec.demand.multiplier.maximum,
    false,
    '×',
  );
  if (multiplier !== null) errors['demand.multiplier'] = multiplier;
  const shift = checkRange(
    parseNumber(form.shift),
    spec.demand.shift_per_day.minimum,
    spec.demand.shift_per_day.maximum,
    false,
    'requests/day',
  );
  if (shift !== null) errors['demand.shift_per_day'] = shift;
  if (
    form.basis === 'm3_marginal_quantile' &&
    !spec.demand.quantile.map(String).includes(form.quantile)
  )
    errors['demand.quantile'] = 'Choose one of the offered quantile levels.';
  for (const p of spec.parameters) {
    const field = form.parameters[p.name];
    if (field === undefined || (p.nullable && field.unlimited)) continue;
    const value = parseNumber(field.text);
    const problem = checkRange(value, p.minimum, p.maximum, INTEGER_PARAMETERS.has(p.name), '');
    if (problem !== null) errors[`parameters.${p.name}`] = problem.replace(/ \.$/, '.');
    // The API's own note: money and penalty rates are 0 or at least 0.01 (the solver would
    // silently drop a smaller nonzero coefficient).
    else if (p.unit.startsWith('usd_per') && typeof value === 'number' && value > 0 && value < 0.01)
      errors[`parameters.${p.name}`] = 'A rate must be 0 or at least 0.01.';
  }
  return errors;
}

function changedNumber(text: string, reference: number | null): number | undefined {
  const value = parseNumber(text);
  if (typeof value === 'string') return undefined;
  return reference !== null && value === reference ? undefined : value;
}

/** The request: only the fields that differ from the reference defaults. */
export function toRequest(form: FormState, spec: SimulationParametersData): SimulationRequest {
  const request: SimulationRequest = {};
  const horizon = changedNumber(form.horizon, spec.horizon_days.default);
  if (horizon !== undefined) request.horizon_days = horizon;
  const multiplier = changedNumber(form.multiplier, spec.demand.multiplier.default);
  const shift = changedNumber(form.shift, spec.demand.shift_per_day.default);
  if (form.basis !== 'point_forecast' || multiplier !== undefined || shift !== undefined) {
    request.demand = {
      basis: form.basis,
      multiplier: multiplier ?? spec.demand.multiplier.default,
      shift_per_day: shift ?? spec.demand.shift_per_day.default,
      ...(form.basis === 'm3_marginal_quantile'
        ? { quantile: Number(form.quantile) as 0.75 | 0.9 | 0.975 }
        : {}),
    };
  }
  const parameters: Record<string, number | null> = {};
  for (const p of spec.parameters) {
    const field = form.parameters[p.name];
    if (field === undefined) continue;
    if (p.nullable && field.unlimited) {
      if (p.default !== null) parameters[p.name] = null;
      continue;
    }
    if (p.nullable && p.default === null) {
      const value = parseNumber(field.text);
      if (typeof value === 'number') parameters[p.name] = value;
      continue;
    }
    const value = changedNumber(field.text, p.default);
    if (value !== undefined) parameters[p.name] = value;
  }
  if (Object.keys(parameters).length > 0) request.parameters = parameters;
  return request;
}

/**
 * The names of what differs from the reference (for the summary and the reset): what the
 * request would change, and every field holding a value that is not valid yet (an edit too).
 */
export function changedFields(form: FormState, spec: SimulationParametersData): string[] {
  const request = toRequest(form, spec);
  const names = new Set([
    ...(request.horizon_days !== undefined ? ['horizon_days'] : []),
    ...(request.demand !== undefined ? ['demand'] : []),
    ...Object.keys(request.parameters ?? {}),
  ]);
  for (const key of Object.keys(validate(form, spec))) {
    if (key.startsWith('parameters.')) names.add(key.slice('parameters.'.length));
    else if (key.startsWith('demand.')) names.add('demand');
    else names.add(key);
  }
  return [...names];
}

/** The form key an API error detail names (`parameters.regular_wage` stays as it is). */
export function fieldOfDetail(field: string): string {
  if (field === 'horizon_days' || field.startsWith('demand.') || field.startsWith('parameters.'))
    return field;
  return '<body>';
}

export const GROUPS: readonly { title: string; names: readonly string[] }[] = [
  {
    title: 'Regular capacity',
    names: ['regular_hours_per_day', 'regular_productivity', 'regular_wage'],
  },
  {
    title: 'Overtime',
    names: [
      'overtime_max_hours_per_day',
      'overtime_total_hours',
      'overtime_productivity',
      'overtime_wage',
    ],
  },
  {
    title: 'Surge capacity',
    names: ['surge_max_per_day', 'surge_total', 'surge_available_from_day', 'surge_price'],
  },
  { title: 'Budget', names: ['discretionary_budget'] },
  {
    title: 'Backlog',
    names: [
      'initial_backlog',
      'terminal_backlog_max',
      'backlog_trigger',
      'backlog_penalty',
      'terminal_penalty',
    ],
  },
];

/** Groups of the parameters the API offers (any new parameter lands in "Other"). */
export function groupParameters(
  parameters: readonly ParameterSpec[],
): { title: string; specs: ParameterSpec[] }[] {
  const known = new Set(GROUPS.flatMap((g) => g.names));
  const groups = GROUPS.map((g) => ({
    title: g.title,
    specs: g.names.flatMap((name) => parameters.filter((p) => p.name === name)),
  }));
  const other = parameters.filter((p) => !known.has(p.name));
  if (other.length > 0) groups.push({ title: 'Other', specs: other });
  return groups.filter((g) => g.specs.length > 0);
}
