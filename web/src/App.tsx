import { Component, lazy, Suspense, useEffect, useRef, useState, type ErrorInfo } from 'react';

import { Close, AtlasMark, Menu } from './components/icons';
import { Loading } from './components/States';
import { Tag } from './components/Tag';
import { DeploymentContext, useDeploymentResource, type Deployment } from './deployment';
import { PAGES, type PagePath } from './pages';
import { Link, useLocation } from './router';

const VIEWS: Record<PagePath, React.LazyExoticComponent<() => React.ReactNode>> = {
  '/': lazy(() => import('./views/Overview')),
  '/forecasting': lazy(() => import('./views/Forecasting')),
  '/uncertainty': lazy(() => import('./views/Uncertainty')),
  '/decisions': lazy(() => import('./views/Decisions')),
  '/simulator': lazy(() => import('./views/Simulator')),
  '/provenance': lazy(() => import('./views/Provenance')),
};

function isPage(path: string): path is PagePath {
  return PAGES.some((page) => page.path === path);
}

/** A failed page load (most often: the dashboard was updated while this tab was open). */
class PageBoundary extends Component<{ children: React.ReactNode }, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('page failed to render', error, info.componentStack);
  }

  override render() {
    if (!this.state.failed) return this.props.children;
    return (
      <div className="state" data-kind="error" role="alert">
        <p className="state-title">This page could not be displayed.</p>
        <p>
          The dashboard may have been updated since this tab was opened, or a part of it failed to
          load. Reloading fetches the current version.
        </p>
        <button
          type="button"
          className="button"
          onClick={() => {
            window.location.reload();
          }}
        >
          Reload
        </button>
      </div>
    );
  }
}

function DeploymentBadge({ deployment }: { deployment: Deployment }) {
  const profile = deployment.descriptor?.profile;
  if (profile === 'public-demo') return <Tag tone="neutral">Public demo · read-only</Tag>;
  if (profile === 'private') return <Tag tone="neutral">Private deployment</Tag>;
  // A local API with a key (also behind the SSH tunnel) refuses the browser's simulations.
  if (profile === 'local')
    return (
      <Tag tone="neutral">
        {deployment.descriptor?.simulations === 'enabled' ? 'Local API' : 'Local API · read-only'}
      </Tag>
    );
  return null;
}

function NotFound() {
  return (
    <div className="stack-3">
      <h1>Page not found</h1>
      <p className="secondary">There is no dashboard page at this address. The pages are:</p>
      <ul>
        {PAGES.map((p) => (
          <li key={p.path}>
            <Link href={p.path}>{p.label}</Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function App() {
  const deployment = useDeploymentResource();
  const { pathname } = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const main = useRef<HTMLElement>(null);
  const menuButton = useRef<HTMLButtonElement>(null);
  const header = useRef<HTMLElement>(null);
  const nav = useRef<HTMLElement>(null);
  const first = useRef(true);
  const page = PAGES.find((candidate) => candidate.path === pathname);

  // The open navigation menu (phones) lies over the page: Escape from within it closes it
  // and returns to its button, and the focus moving on into the page closes it, so it
  // never covers the focused element. Escape elsewhere belongs to the focused control (a
  // chart clears its reading).
  useEffect(() => {
    if (!menuOpen) return;
    const inMenu = (target: EventTarget | null) =>
      target === document.body ||
      (target instanceof Node &&
        (header.current?.contains(target) === true || nav.current?.contains(target) === true));
    const close = (event: KeyboardEvent) => {
      if (event.key !== 'Escape' || !inMenu(event.target)) return;
      setMenuOpen(false);
      menuButton.current?.focus();
    };
    const leave = (event: FocusEvent) => {
      if (!inMenu(event.target)) setMenuOpen(false);
    };
    document.addEventListener('keydown', close);
    document.addEventListener('focusin', leave);
    return () => {
      document.removeEventListener('keydown', close);
      document.removeEventListener('focusin', leave);
    };
  }, [menuOpen]);

  // The top bar's height, where the open phone menu starts (the bar grows when its tags
  // wrap or the text is enlarged).
  useEffect(() => {
    const bar = header.current;
    if (bar === null || typeof ResizeObserver === 'undefined') return;
    const root = document.documentElement;
    const observer = new ResizeObserver(() => {
      root.style.setProperty('--topbar-height', `${bar.offsetHeight}px`);
    });
    observer.observe(bar);
    return () => {
      observer.disconnect();
      root.style.removeProperty('--topbar-height');
    };
  }, []);

  useEffect(() => {
    document.title = `${page?.title ?? 'Page not found'} · ATLAS`;
    if (first.current) {
      first.current = false;
      return;
    }
    // A new page: start at its top, and let assistive technology announce it.
    window.scrollTo({ top: 0 });
    main.current?.focus({ preventScroll: true });
  }, [pathname, page]);

  const View = isPage(pathname) ? VIEWS[pathname] : NotFound;
  return (
    <DeploymentContext.Provider value={deployment}>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <div className="shell">
        <header className="topbar" ref={header}>
          <button
            ref={menuButton}
            type="button"
            className="menu-button"
            aria-expanded={menuOpen}
            aria-controls="site-nav"
            aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
            onClick={() => {
              setMenuOpen(!menuOpen);
            }}
          >
            {menuOpen ? <Close width={22} height={22} /> : <Menu width={22} height={22} />}
          </button>
          <Link href="/" className="brand">
            <AtlasMark />
            <span className="brand-name">ATLAS</span>
          </Link>
          <span className="research-tag tag-row">
            <DeploymentBadge deployment={deployment} />
            <Tag
              tone="warning"
              title="Research software: nothing here is a validated staffing, budget or capacity recommendation"
            >
              Research software
            </Tag>
          </span>
        </header>
        <nav
          ref={nav}
          id="site-nav"
          className="sidebar"
          data-open={menuOpen ? 'true' : 'false'}
          aria-label="Dashboard sections"
        >
          <ul className="nav-list">
            {PAGES.map((item) => (
              <li key={item.path}>
                <Link
                  href={item.path}
                  className="nav-link"
                  current={item.path === pathname}
                  onNavigate={() => {
                    setMenuOpen(false);
                  }}
                >
                  <span className="nav-label">{item.label}</span>
                  <span className="nav-summary">{item.summary}</span>
                </Link>
              </li>
            ))}
          </ul>
          <div className="sidebar-footer">
            <p>
              NYC 311 service requests, frozen snapshot to 30 Jun 2026. Forecasts are archived
              projections, not live predictions.
            </p>
            <p>Not a validated staffing, budget or service-capacity system.</p>
          </div>
        </nav>
        <main id="main" className="main" tabIndex={-1} ref={main}>
          <div className="content page-enter" key={pathname}>
            <PageBoundary>
              <Suspense fallback={<Loading label="this page" />}>
                <View />
              </Suspense>
            </PageBoundary>
          </div>
        </main>
        <footer className="footer">
          <div className="content">
            <p>
              ATLAS research dashboard {__ATLAS_DASHBOARD_VERSION__}. Data: NYC Open Data, 311
              Service Requests (dataset erm2-nwe9), frozen and hash-verified as the legacy-v0
              snapshot. Every number is served by the ATLAS API from committed, verified research
              outputs.
            </p>
            <p>
              Nothing here is a validated staffing, budget or service-capacity recommendation for
              any NYC agency.
            </p>
          </div>
        </footer>
      </div>
    </DeploymentContext.Provider>
  );
}
