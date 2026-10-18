/**
 * The dashboard's pages: the single source of truth for navigation, document titles and
 * the client routes a server must answer with `index.html`.
 *
 * The build writes these paths into `dist/atlas-dashboard.json`; the API's dashboard
 * mount and the nginx site (rendered by `atlas-deploy`) route exactly these paths and
 * the built files, nothing else. Keep this module free of React imports: the build
 * reads it in Node.
 */
export interface PageInfo {
  /** The URL path (a fixed string: servers route it exactly). */
  readonly path: string;
  /** The navigation label. */
  readonly label: string;
  /** The document title prefix. */
  readonly title: string;
  /** One line under the label in the navigation. */
  readonly summary: string;
  /** (M9) The line in the static profile, where it differs (nothing runs there). */
  readonly staticSummary?: string;
}

export const PAGES = [
  {
    path: '/',
    label: 'Overview',
    title: 'Overview',
    summary: 'Status, data and headline results',
  },
  {
    path: '/forecasting',
    label: 'Forecasting',
    title: 'Forecasting',
    summary: 'History, archived projection, backtests',
  },
  {
    path: '/uncertainty',
    label: 'Uncertainty & risk',
    title: 'Uncertainty and risk',
    summary: 'M3 intervals and their calibration',
  },
  {
    path: '/decisions',
    label: 'Decision optimisation',
    title: 'Decision optimisation',
    summary: 'M4 reference scenarios',
  },
  {
    path: '/simulator',
    label: 'Simulator',
    title: 'Hypothetical simulator',
    summary: 'Bounded what-if runs of the M4 engine',
    staticSummary: 'Recorded M4 reference scenarios',
  },
  {
    path: '/provenance',
    label: 'Methods & provenance',
    title: 'Methods and provenance',
    summary: 'Sources, fingerprints, limitations',
  },
] as const satisfies readonly PageInfo[];

export type PagePath = (typeof PAGES)[number]['path'];

export const PAGE_PATHS: readonly PagePath[] = PAGES.map((page) => page.path);
