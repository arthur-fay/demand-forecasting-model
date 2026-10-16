import { describe, expect, it, vi } from 'vitest';

import {
  addDays,
  DASH,
  formatDate,
  formatInteger,
  formatP,
  formatPct,
  formatShare,
  formatSigned,
  formatUnit,
  formatUsd,
  humanise,
  modelName,
  shortHash,
} from './format';

describe('numbers', () => {
  it('never shows a missing or non-finite value as a number', () => {
    for (const value of [null, undefined, Number.NaN, Infinity]) {
      expect(formatInteger(value)).toBe(DASH);
      expect(formatUsd(value)).toBe(DASH);
      expect(formatSigned(value)).toBe(DASH);
    }
  });

  it('rounds for reading only', () => {
    expect(formatInteger(984.9183743068235)).toBe('985');
    expect(formatSigned(-127.25345109000193)).toBe('−127');
    expect(formatSigned(1.9379106212822421)).toBe('+2');
    expect(formatSigned(-0.2)).toBe('0');
    expect(formatUsd(225275.79752229623)).toBe('$225,276');
    expect(formatShare(0.7990079365079366)).toBe('79.9 %');
    expect(formatPct(9.236367268327673)).toBe('9.2 %');
    expect(formatP(0.0535166597203122)).toBe('0.054');
    expect(formatP(0.0001)).toBe('< 0.001');
  });
});

describe('dates', () => {
  it('runs in a time zone west of UTC (vite.config.ts), where a local-time date shifts', () => {
    // CI runs in UTC, where formatting a date in local time cannot be told apart.
    expect(process.env.TZ).toBe('America/Los_Angeles');
    expect(new Date(Date.UTC(2026, 6, 1)).getTimezoneOffset()).toBe(420);
  });

  it('formats calendar dates in UTC, whatever the time zone', () => {
    expect(formatDate('2026-06-30')).toBe('Jun 30, 2026');
    expect(addDays('2026-06-30', 1)).toBe('2026-07-01');
    expect(addDays('2026-06-30', -179)).toBe('2026-01-02');
  });

  // A UTC midnight formatted in local time is a day early west of UTC; a local midnight
  // formatted in UTC is a day early east of it. The module is loaded again in each zone (its
  // formatters take the zone when they are created).
  for (const [zone, offset] of [
    ['Pacific/Pago_Pago', 660],
    ['America/Los_Angeles', 420],
    ['Pacific/Kiritimati', -840],
  ] as const) {
    it(`shows the same calendar days in ${zone}`, async () => {
      const before = process.env.TZ;
      process.env.TZ = zone;
      try {
        vi.resetModules();
        const format = await import('./format');
        expect(new Date(Date.UTC(2026, 6, 1)).getTimezoneOffset()).toBe(offset);
        expect(format.formatDate('2026-06-30')).toBe('Jun 30, 2026');
        expect(format.formatDate('2026-07-01')).toBe('Jul 1, 2026');
        expect(format.formatWeekdayDate('2026-07-03')).toBe('Fri, Jul 3, 2026');
        expect(format.formatShortDate(format.dateValue('2026-07-01'))).toBe('Jul 1');
        expect(format.formatMonthYear(format.dateValue('2026-07-01'))).toBe('Jul 2026');
        expect(format.dateValue('2026-07-01')).toBe(Date.UTC(2026, 6, 1));
        expect(format.isoDay(format.dateValue('2026-07-01'))).toBe('2026-07-01');
        expect(format.addDays('2026-06-30', 1)).toBe('2026-07-01');
      } finally {
        if (before === undefined) delete process.env.TZ;
        else process.env.TZ = before;
        vi.resetModules();
      }
    });
  }

  it('refuses something that is not a calendar date', () => {
    expect(() => formatDate('30/06/2026')).toThrow();
  });
});

describe('names and units', () => {
  it('keeps unknown units as given', () => {
    expect(formatUnit('requests_per_day')).toBe('requests/day');
    expect(formatUnit('usd_equivalent (ranking score, not a cost)')).toContain('not a cost');
    expect(formatUnit('widgets_per_fortnight')).toBe('widgets per fortnight');
  });

  it('names models and identifiers', () => {
    expect(modelName('atlas_linear_regression')).toBe('ATLAS linear regression');
    expect(modelName('new_model')).toBe('New model');
    expect(humanise('reduced_budget')).toBe('Reduced budget');
    expect(shortHash('0123456789abcdef', 8)).toBe('01234567');
    expect(shortHash(null)).toBe(DASH);
  });
});
