/**
 * A minimal History-API router for six fixed pages (no dependency). Links are real
 * anchors (they work without JavaScript as full page loads: the servers answer every
 * page path with index.html). Navigation moves focus to the new page's heading and
 * updates the document title, so screen-reader users hear the page change.
 */
import { useSyncExternalStore } from 'react';

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

let cached: Location = { pathname: '/', search: '' };

function snapshot(): Location {
  const { pathname, search } = window.location;
  if (cached.pathname !== pathname || cached.search !== search) cached = { pathname, search };
  return cached;
}

export function useLocation(): Location {
  return useSyncExternalStore(subscribe, snapshot, snapshot);
}

/** Go to `href` (same origin, a path with an optional query). */
export function navigate(href: string, { replace = false }: { replace?: boolean } = {}): void {
  if (!href.startsWith('/') || href.startsWith('//'))
    throw new Error(`not an in-app path: ${href}`);
  if (replace) window.history.replaceState(null, '', href);
  else window.history.pushState(null, '', href);
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
  navigate(`${url.pathname}${url.search}`, { replace: true });
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
      href={href}
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
