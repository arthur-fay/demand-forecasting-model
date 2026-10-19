/** The dashboard's version (web/package.json), injected at build time. */
declare const __ATLAS_DASHBOARD_VERSION__: string;

/**
 * (M9) The build profile (vite.config.ts): true for the static profile (a static export),
 * false for the api profile. A literal, so the api build drops the code only the static
 * profile runs (its transport, reader and recorded-scenario panel). Some static-only wording
 * stays in the api build, never shown: the navigation line, the deployment badge, the
 * Provenance and Simulator explanations and two error messages.
 */
declare const __ATLAS_STATIC__: boolean;

/**
 * (M9) The static export's files, by the API request each one answers (static profile
 * only; null in the api profile): compiled into the build from
 * web/static-export/atlas-static-export.json, so the browser can check every file it reads.
 */
declare const __ATLAS_STATIC_INDEX__: Readonly<
  Record<string, { readonly file: string; readonly sha256: string; readonly bytes: number }>
> | null;
