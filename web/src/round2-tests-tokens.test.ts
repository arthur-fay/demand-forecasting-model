/**
 * The colour tokens keep what docs/DASHBOARD.md section 8 says of them, in both themes:
 * control borders and data marks at least 3:1 against the surface they are drawn on
 * (WCAG 1.4.11), and every pair of series colours told apart under a red-green
 * colour-vision deficiency.
 */
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(join(import.meta.dirname, 'styles', 'tokens.css'), 'utf-8');
const blocks = css.split('@media (prefers-color-scheme: dark)');

function tokens(block: string): Record<string, string> {
  return Object.fromEntries(
    [...block.matchAll(/--([a-z0-9-]+): (#[0-9a-f]{6});/g)].map((m) => [m[1]!, m[2]!]),
  );
}

const light = tokens(blocks[0]!);
const THEMES = { light, dark: { ...light, ...tokens(blocks[1]!) } };

/** sRGB hex to linear RGB. */
function linear(hex: string): [number, number, number] {
  const [r, g, b] = [1, 3, 5].map((at) => {
    const c = parseInt(hex.slice(at, at + 2), 16) / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return [r!, g!, b!];
}

/** The WCAG contrast ratio of two colours. */
function contrast(a: string, b: string): number {
  const [high, low] = [a, b]
    .map((hex) => {
      const [r, g, bl] = linear(hex);
      return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
    })
    .sort((x, y) => y - x);
  return (high! + 0.05) / (low! + 0.05);
}

// Machado, Oliveira and Fernandes (2009) at severity 1.0, on linear RGB.
const DEFICIENCIES = {
  protanopia: [
    [0.152286, 1.052583, -0.204868],
    [0.114503, 0.786281, 0.099216],
    [-0.003882, -0.048116, 1.051998],
  ],
  deuteranopia: [
    [0.367322, 0.860646, -0.227968],
    [0.280085, 0.672501, 0.047413],
    [-0.01182, 0.04294, 0.968881],
  ],
} as const;

/** OKLab of a linear RGB colour. */
function oklab([r, g, b]: readonly number[]): [number, number, number] {
  const l = Math.cbrt(0.4122214708 * r! + 0.5363325363 * g! + 0.0514459929 * b!);
  const m = Math.cbrt(0.2119034982 * r! + 0.6806995451 * g! + 0.1073969566 * b!);
  const s = Math.cbrt(0.0883024619 * r! + 0.2817188376 * g! + 0.6299787005 * b!);
  return [
    0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
    1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
    0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s,
  ];
}

/** ΔE (OKLab distance × 100) of two colours as a reader with `deficiency` sees them. */
function difference(a: string, b: string, deficiency: keyof typeof DEFICIENCIES): number {
  const seen = (hex: string) => {
    const rgb = linear(hex);
    return oklab(
      DEFICIENCIES[deficiency].map((row) =>
        Math.min(1, Math.max(0, row[0] * rgb[0] + row[1] * rgb[1] + row[2] * rgb[2])),
      ),
    );
  };
  const [x, y] = [seen(a), seen(b)];
  return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}

const SERIES = ['series-1', 'series-2', 'series-3'];

describe.each(Object.entries(THEMES))('the %s theme', (_name, theme) => {
  const token = (name: string): string => {
    expect(theme[name], `--${name}`).toMatch(/^#[0-9a-f]{6}$/);
    return theme[name]!;
  };

  it('draws control borders and the neutral mark at 3:1 or more on every surface', () => {
    for (const mark of ['control-border', 'neutral-mark'])
      for (const surface of ['page', 'surface', 'surface-sunken'])
        expect(
          contrast(token(mark), token(surface)),
          `${mark} on ${surface}`,
        ).toBeGreaterThanOrEqual(3);
  });

  it('draws every series at 3:1 or more on the panel surface its chart is on', () => {
    for (const mark of SERIES)
      expect(contrast(token(mark), token('surface')), mark).toBeGreaterThanOrEqual(3);
  });

  it('keeps every pair of series colours apart under protanopia and deuteranopia', () => {
    for (const deficiency of ['protanopia', 'deuteranopia'] as const)
      for (const [i, a] of SERIES.entries())
        for (const b of SERIES.slice(i + 1))
          expect(
            difference(token(a), token(b), deficiency),
            `${a} and ${b}, ${deficiency}`,
          ).toBeGreaterThanOrEqual(8);
  });
});
