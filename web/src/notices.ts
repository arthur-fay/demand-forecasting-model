/**
 * The licence notices every build ships next to the code (docs/DASHBOARD.md section 2, the
 * inventory; section 7, how they are served).
 *
 * The bundle contains minified copies of MIT and ISC packages and the Inter font
 * (SIL Open Font License 1.1), whose licences require the copyright and licence text with
 * every copy:
 *
 * - {@link THIRD_PARTY_NOTICES} is written by Vite's `build.license` (vite.config.ts): the
 *   name, version, licence and licence text of every package bundled into the code;
 * - {@link FONT_LICENCE} is the Inter package's own LICENSE (copyright and OFL-1.1 text),
 *   copied unchanged into `public/`, so it ships next to the font.
 *
 * Both are listed in the build's manifest and served like every other built file; the
 * footer links to them. Keep this module free of React imports: the build reads it in Node.
 */
export const THIRD_PARTY_NOTICES = 'third-party-licences.txt';
export const FONT_LICENCE = 'inter-licence.txt';
