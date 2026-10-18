/** (M9) App paths under a base path, and the addresses a static host also answers. */
import { describe, expect, it } from 'vitest';

import { afterEach } from 'vitest';

import { appHref, appPath, canonicalise, navigate } from './router';

const BASE = '/demand-forecasting-model/';

describe('the api profile (base /) uses every path exactly as it is', () => {
  it('maps paths and links to themselves', () => {
    for (const path of ['/', '/forecasting', '/forecasting/', '/forecasting.html', '/x'])
      expect(appPath(path, '/', false)).toBe(path);
    expect(appHref('/decisions?scenario=normal#scenario-detail', '/')).toBe(
      '/decisions?scenario=normal#scenario-detail',
    );
  });
});

describe('the static profile', () => {
  it('reads app paths under the base, the base itself with or without its slash', () => {
    expect(appPath(BASE, BASE, true)).toBe('/');
    expect(appPath('/demand-forecasting-model', BASE, true)).toBe('/');
    expect(appPath(`${BASE}forecasting`, BASE, true)).toBe('/forecasting');
    // A path outside the base has no app path: '', which no page has.
    expect(appPath('/forecasting', BASE, true)).toBe('');
    expect(appPath('/', BASE, true)).toBe('');
    expect(appPath('/demand-forecasting-modelx/forecasting', BASE, true)).toBe('');
  });

  it('never reads an address that would leave the base as a page (dot segments)', () => {
    for (const rest of [
      '...html',
      '...html/',
      '..html',
      '%2e%2e.html',
      '.%2e.html',
      '%2E%2E.html/',
      'forecasting/...html',
      'a/b/...html/',
    ])
      expect(appPath(`${BASE}${rest}`, BASE, true), rest).toBe('');
    // A dot inside a name is not a dot segment.
    expect(appPath(`${BASE}forecasting/...x`, BASE, true)).toBe('/forecasting/...x');
  });

  it('shows the other forms a static host answers as the page they name', () => {
    expect(appPath(`${BASE}forecasting.html`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}forecasting/`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}index.html`, BASE, true)).toBe('/');
    expect(appPath(`${BASE}404.html`, BASE, true)).toBe('/404');
  });

  it('reads repeated slashes, suffixes and trailing slashes as one page', () => {
    expect(appPath(`${BASE}/forecasting`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}//forecasting`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}forecasting//`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}forecasting.html.html`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}forecasting.html/`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}.html`, BASE, true)).toBe('/');
    expect(appPath(`${BASE}decisions/scenario//x`, BASE, true)).toBe('/decisions/scenario/x');
  });

  it('links app paths under the base', () => {
    expect(appHref('/', BASE)).toBe(BASE);
    expect(appHref('/simulator?scenario=normal', BASE)).toBe(`${BASE}simulator?scenario=normal`);
  });

  it('refuses anything that is not an app path', () => {
    for (const href of ['forecasting', '//example.org/', 'https://example.org/']) {
      expect(() => appHref(href, BASE)).toThrow(/not an in-app path/);
      expect(() => {
        navigate(href);
      }).toThrow(/not an in-app path/);
    }
  });
});

describe('the canonical address of a page (static profile)', () => {
  afterEach(() => {
    window.history.replaceState(null, '', '/');
  });

  const at = (href: string) => {
    window.history.replaceState(null, '', href);
    canonicalise(BASE);
    return `${window.location.pathname}${window.location.search}${window.location.hash}`;
  };

  it('keeps the query and the fragment', () => {
    expect(at(`${BASE}forecasting.html?window=90#frag`)).toBe(`${BASE}forecasting?window=90#frag`);
    expect(at(`${BASE}index.html?x=1#y`)).toBe(`${BASE}?x=1#y`);
    expect(at(`${BASE}uncertainty/`)).toBe(`${BASE}uncertainty`);
  });

  it('never fails on an address it cannot use, and leaves other paths alone', () => {
    expect(at(`${BASE}/forecasting`)).toBe(`${BASE}forecasting`);
    expect(at(`${BASE}//evil.example/x`)).toBe(`${BASE}evil.example/x`);
    expect(at('/elsewhere//x.html')).toBe('/elsewhere//x.html');
    expect(at(`${BASE}forecasting`)).toBe(`${BASE}forecasting`);
  });

  it('never moves the address out of the base', () => {
    for (const rest of [
      '...html',
      '...html?x=1#y',
      '%2e%2e.html',
      '.%2e.html',
      '%2E%2E.html/',
      'forecasting/...html',
      'a/b/...html/',
    ])
      expect(at(`${BASE}${rest}`), rest).toBe(`${BASE}${rest}`);
  });
});
