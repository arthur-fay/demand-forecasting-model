/** (M9) App paths under a base path, and the addresses a static host also answers. */
import { describe, expect, it } from 'vitest';

import { appHref, appPath, navigate } from './router';

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
    // A path outside the base is no page.
    expect(appPath('/forecasting', BASE, true)).toBe('/forecasting');
    expect(appPath('/demand-forecasting-modelx/forecasting', BASE, true)).toBe(
      '/demand-forecasting-modelx/forecasting',
    );
  });

  it('shows the other forms a static host answers as the page they name', () => {
    expect(appPath(`${BASE}forecasting.html`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}forecasting/`, BASE, true)).toBe('/forecasting');
    expect(appPath(`${BASE}index.html`, BASE, true)).toBe('/');
    expect(appPath(`${BASE}404.html`, BASE, true)).toBe('/404');
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
