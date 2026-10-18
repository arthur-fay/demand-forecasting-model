/**
 * A minimal History-API router for six fixed pages (no dependency). Links are real
 * anchors (they work without JavaScript as full page loads: the servers answer every
 * page path with index.html). Navigation moves focus to the new page's heading and
 * updates the document title, so screen-reader users hear the page change.
 *
 * (M9) The app's own paths (`/`, `/forecasting`, ...) are the same in every profile; the
 * URL puts them under the build's base path (`profile.ts`: `/` for the API and nginx, the
 * repository's path on GitHub Pages). A static host cannot rewrite a request, so the
 * static build also has each page as `<page>.html` and a `404.html` that is the app itself
 * (`build/static-site.ts`); here a page reached as `forecasting.html`, `index.html` or with
 * a trailing slash is shown at its canonical address. In the api profile the base is `/`
 * and every path is used exactly as it is.
 */
import { useSyncExternalStore } from 'react';

import { BASE, STATIC } from './profile';

const EVENT = 'atlas:navigate';

function subscribe(callback: () => void): () => void {
  window.addEventListener('popstate', callback);
  window.addEventListener(EVENT, callback);
  return () => {
    window.removeEventListener('popstate', callback);
    window.removeEventListener(EVENT, callback);
  };
}

export interface Location {
  readonly pathname: string;
  readonly search: string;
}

/** A dot segment, as a URL reads it (`%2e` is a dot). */
const DOT_SEGMENT = /^(?:\.|%2e){1,2}$/i;

/**
 * The app path of a URL path under `base`: `/demand-forecasting-model/forecasting` is
 * `/forecasting`, and the base itself, with or without its slash, is `/`. A path outside
 * the base has none: `''`, which no page has. With `canonical` (static hosts), the forms
 * a static host also answers (`/forecasting.html`, `/index.html`, `/forecasting/`) are
 * the page they name.
 */
export function appPath(pathname: string, base = BASE, canonical = STATIC): string {
  let path = pathname;
  if (base !== '/') {
    if (pathname === base.slice(0, -1)) path = '/';
    else if (pathname.startsWith(base)) path = pathname.slice(base.length - 1);
    else return '';
  }
  if (!canonical) return path;
  // Repeated slashes are one (a static host answers `//forecasting` with 404.html, and
  // `//...` is no app path), then every trailing slash and `.html` comes off.
  path = path.replace(/\/{2,}/g, '/');
  for (let before = ''; before !== path;) {
    before = path;
    if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
    if (path.endsWith('.html')) path = path.slice(0, -'.html'.length);
  }
  // What is left may hold a dot segment (`...html` leaves `..`), which a URL resolves
  // away, out of the base: such an address is no page.
  if (path.split('/').some((segment) => DOT_SEGMENT.test(segment))) return '';
  return path === '/index' ? '/' : path;
}

/** The URL of an app path (with an optional query or fragment) under `base`. */
export function appHref(path: string, base = BASE): string {
  if (!path.startsWith('/') || path.startsWith('//'))
    throw new Error(`not an in-app path: ${path}`);
  return base === '/' ? path : `${base}${path.slice(1)}`;
}

let cached: Location = { pathname: '/', search: '' };

function snapshot(): Location {
  const pathname = appPath(window.location.pathname);
  const { search } = window.location;
  if (cached.pathname !== pathname || cached.search !== search) cached = { pathname, search };
  return cached;
}

/**
 * (static profile) Show the page at its canonical address once, before the first render:
 * `.../forecasting.html` becomes `.../forecasting`, so links, reloads and the back button
 * all use one address per page.
 */
export function canonicalise(base = BASE): void {
  const { href: current, pathname, search, hash } = window.location;
  const path = appPath(pathname, base, true);
  // Never fatal, never out of the base: an address the router cannot rewrite stays as it
  // is (its page, or "Page not found", still shows), and so does one whose rewritten
  // form a URL would read as another path.
  if (!path.startsWith('/') || path.startsWith('//')) return;
  const href = appHref(path, base);
  if (href === pathname || new URL(href, current).pathname !== href) return;
  window.history.replaceState(null, '', `${href}${search}${hash}`);
}

if (STATIC) canonicalise();

export function useLocation(): Location {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Go to `href` (an app path with an optional query). */
export function navigate(href: string, { replace = false }: { replace?: boolean } = {}): void {
  const url = appHref(href);
  if (replace) window.history.replaceState(null, '', url);
  else window.history.pushState(null, '', url);
  window.dispatchEvent(new Event(EVENT));
}

/** Read one query parameter of the current location. */
export function useQueryParam(name: string): string | null {
  const { search } = useLocation();
  return new URLSearchParams(search).get(name);
}

/** Replace one query parameter (no new history entry: filters are not pages). */
export function setQueryParam(name: string, value: string | null): void {
  const url = new URL(window.location.href);
  if (value === null) url.searchParams.delete(name);
  else url.searchParams.set(name, value);
  navigate(`${appPath(url.pathname)}${url.search}`, { replace: true });
}

export function Link({
  href,
  children,
  className,
  current,
  onNavigate,
}: {
  href: string;
  children: React.ReactNode;
  className?: string;
  current?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <a
      href={appHref(href)}
      className={className}
      aria-current={current === true ? 'page' : undefined}
      onClick={(event) => {
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        )
          return;
        event.preventDefault();
        navigate(href);
        onNavigate?.();
      }}
    >
      {children}
    </a>
  );
}
