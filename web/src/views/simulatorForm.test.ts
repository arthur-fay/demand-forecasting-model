import { describe, expect, it } from 'vitest';

import { SimulationParametersData } from '../api/schemas';
import { z } from '../api/zod';
import { FIXTURES } from '../test/api';
import {
  changedFields,
  fieldOfDetail,
  groupParameters,
  initialForm,
  parseNumber,
  toRequest,
  validate,
  type FormState,
} from './simulatorForm';

const spec = z.parse(SimulationParametersData, FIXTURES.parameters.data);

function withParameter(form: FormState, name: string, text: string, unlimited = false): FormState {
  return { ...form, parameters: { ...form.parameters, [name]: { text, unlimited } } };
}

describe('the reference form', () => {
  it('is valid and sends nothing (every field takes its reference value)', () => {
    const form = initialForm(spec);
    expect(validate(form, spec)).toEqual({});
    expect(toRequest(form, spec)).toEqual({});
    expect(changedFields(form, spec)).toEqual([]);
  });

  it('marks limits without a reference value as unlimited', () => {
    const form = initialForm(spec);
    expect(form.parameters.surge_total).toEqual({ text: '', unlimited: true });
    expect(form.parameters.discretionary_budget?.unlimited).toBe(false);
  });
});

describe('validation', () => {
  it.each([
    ['', 'Enter a value.'],
    ['abc', 'Enter a number'],
    ['1e400', 'finite'],
    ['12 000', 'Enter a number'],
  ])('refuses %j', (text, message) => {
    const errors = validate(withParameter(initialForm(spec), 'initial_backlog', text), spec);
    expect(errors['parameters.initial_backlog']).toContain(message);
  });

  it('enforces the published ranges', () => {
    const errors = validate(withParameter(initialForm(spec), 'regular_productivity', '0.05'), spec);
    expect(errors['parameters.regular_productivity']).toBe('Must be between 0.1 and 10.');
    const horizon = validate({ ...initialForm(spec), horizon: '31' }, spec);
    expect(horizon.horizon_days).toContain('between 1 and 30');
  });

  it('requires whole numbers where the API does', () => {
    expect(validate({ ...initialForm(spec), horizon: '7.5' }, spec).horizon_days).toBe(
      'Enter a whole number.',
    );
    const errors = validate(
      withParameter(initialForm(spec), 'surge_available_from_day', '2.5'),
      spec,
    );
    expect(errors['parameters.surge_available_from_day']).toBe('Enter a whole number.');
  });

  it('applies the API note: money rates are 0 or at least 0.01', () => {
    expect(
      validate(withParameter(initialForm(spec), 'regular_wage', '0.001'), spec)[
        'parameters.regular_wage'
      ],
    ).toBe('A rate must be 0 or at least 0.01.');
    expect(validate(withParameter(initialForm(spec), 'regular_wage', '0'), spec)).toEqual({});
  });

  it('does not validate a limit the user removed', () => {
    const form = withParameter(initialForm(spec), 'terminal_backlog_max', 'nonsense', true);
    expect(validate(form, spec)).toEqual({});
  });

  it('parses numbers strictly', () => {
    expect(parseNumber(' 42 ')).toBe(42);
    expect(parseNumber('.5')).toBe(0.5);
    expect(parseNumber('0x10')).toContain('Enter a number');
  });
});

describe('the request', () => {
  it('carries only what changed', () => {
    let form = { ...initialForm(spec), horizon: '14' };
    form = withParameter(form, 'discretionary_budget', '150000');
    expect(toRequest(form, spec)).toEqual({
      horizon_days: 14,
      parameters: { discretionary_budget: 150000 },
    });
    expect(changedFields(form, spec)).toEqual(['horizon_days', 'discretionary_budget']);
  });

  it('removes a limit with an explicit null', () => {
    const form = withParameter(initialForm(spec), 'terminal_backlog_max', '4000', true);
    expect(toRequest(form, spec)).toEqual({ parameters: { terminal_backlog_max: null } });
  });

  it('sets a limit the reference leaves open', () => {
    const form = withParameter(initialForm(spec), 'surge_total', '10000');
    expect(toRequest(form, spec)).toEqual({ parameters: { surge_total: 10000 } });
  });

  it('sends the whole demand block when any of it changes', () => {
    const form: FormState = {
      ...initialForm(spec),
      basis: 'm3_marginal_quantile',
      quantile: '0.9',
    };
    expect(toRequest(form, spec)).toEqual({
      demand: { basis: 'm3_marginal_quantile', multiplier: 1, shift_per_day: 0, quantile: 0.9 },
    });
    expect(toRequest({ ...initialForm(spec), multiplier: '1.1' }, spec)).toEqual({
      demand: { basis: 'point_forecast', multiplier: 1.1, shift_per_day: 0 },
    });
  });
});

describe('API errors on the form', () => {
  it('maps every error field to a control (one rule for every layer)', () => {
    expect(fieldOfDetail('parameters.regular_wage')).toBe('parameters.regular_wage');
    expect(fieldOfDetail('demand.multiplier')).toBe('demand.multiplier');
    expect(fieldOfDetail('horizon_days')).toBe('horizon_days');
    expect(fieldOfDetail('<body>')).toBe('<body>');
    expect(fieldOfDetail('something_else')).toBe('<body>');
  });

  it('groups every parameter the API offers, unknown ones under Other', () => {
    const groups = groupParameters(spec.parameters);
    expect(groups.flatMap((g) => g.specs).length).toBe(spec.parameters.length);
    const extra = groupParameters([
      ...spec.parameters,
      { ...spec.parameters[0]!, name: 'new_limit' },
    ]);
    expect(extra.at(-1)?.title).toBe('Other');
  });
});
