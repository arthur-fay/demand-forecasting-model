// (M9) A local stand-in for GitHub Pages, serving the static build (dist-pages/) the way
// Pages serves a project site, for the browser tests and `npm run preview:pages`:
//
// - the site lives under its base path (/demand-forecasting-model/); the base without its
//   slash is redirected (301) to it, and nothing else outside it is the site's (404);
// - a path is a file, else a directory (301 to the path with a slash, which then serves its
//   index.html), else `<path>.html` (GitHub Pages serves `/forecasting` from
//   `forecasting.html`); anything else is the site's 404.html, with status 404;
// - no rewrite, no header of the site's own: no Content-Security-Policy (the build carries
//   its policy in the page), `Cache-Control: max-age=600` and an ETag, as Pages sends;
// - GET and HEAD only; a path with an encoded dot, slash or backslash (`%2E`, `%2F`, `%5C`),
//   a backslash, NUL or a dot segment is never a file: it is answered with 404.html (a
//   stand-in stricter than a host that decodes them is the safer one to test against).
//
// It reads nothing outside the build directory: every file it serves, 404.html and
// index.html included, is resolved through its links and must stay inside. It is not GitHub Pages: the real behaviour
// is verified once Pages is enabled (docs/STATIC_DEMO.md, "After activation").
//
//   node scripts/pages-server.mjs [--dir dist-pages] [--base /demand-forecasting-model/]
//                                 [--host 127.0.0.1] [--port 4180]
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync, statSync } from 'node:fs';
import { createServer } from 'node:http';
import { extname, resolve, sep } from 'node:path';
import { parseArgs } from 'node:util';

const { values } = parseArgs({
  options: {
    dir: { type: 'string', default: 'dist-pages' },
    base: { type: 'string', default: '/demand-forecasting-model/' },
    host: { type: 'string', default: '127.0.0.1' },
    port: { type: 'string', default: '4180' },
  },
});
const root = realpathSync(resolve(values.dir));
const base = values.base;
if (!/^\/(?:[A-Za-z0-9._-]+\/)*$/.test(base)) throw new Error(`invalid --base ${base}`);

const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'application/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.woff2': 'font/woff2',
  '.txt': 'text/plain; charset=utf-8',
};

/**
 * The regular file at `relative` below the build, or null. Resolved through every link:
 * a link that leads out of the build is not there.
 */
function file(relative) {
  const inside = (path) => path === root || path.startsWith(root + sep);
  const target = resolve(root, relative);
  if (!inside(target)) return null;
  try {
    const real = realpathSync(target);
    if (!inside(real)) return null;
    const stat = statSync(real);
    if (stat.isFile()) return real;
    if (stat.isDirectory()) return 'directory';
  } catch {
    // not there
  }
  return null;
}

const NOT_FOUND = file('404.html');
if (file('index.html') === null || NOT_FOUND === null || NOT_FOUND === 'directory') {
  console.error(`${root} holds no static build: run \`npm run build:pages\` first`);
  process.exit(2);
}

function send(request, response, status, path, extra = {}) {
  const body = readFileSync(path);
  const etag = `"${createHash('sha256').update(body).digest('hex').slice(0, 16)}"`;
  const headers = {
    'Content-Type': TYPES[extname(path)] ?? 'application/octet-stream',
    'Cache-Control': 'max-age=600',
    ETag: etag,
    'Access-Control-Allow-Origin': '*',
    ...extra,
  };
  if (status === 200 && request.headers['if-none-match'] === etag) {
    response.writeHead(304, headers);
    response.end();
    return;
  }
  response.writeHead(status, { ...headers, 'Content-Length': body.length });
  response.end(request.method === 'HEAD' ? undefined : body);
}

function notFound(request, response) {
  send(request, response, 404, NOT_FOUND);
}

const server = createServer((request, response) => {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.writeHead(405, { Allow: 'GET, HEAD', 'Content-Type': 'text/plain' });
    response.end('405 method not allowed\n');
    return;
  }
  const url = new URL(request.url ?? '/', 'http://pages.invalid');
  if (/%(?:2e|2f|5c)/i.test(url.pathname)) {
    notFound(request, response);
    return;
  }
  let path;
  try {
    path = decodeURIComponent(url.pathname);
  } catch {
    notFound(request, response);
    return;
  }
  if (path === base.slice(0, -1) && base !== '/') {
    response.writeHead(301, { Location: `${base}${url.search}` });
    response.end();
    return;
  }
  if (!path.startsWith(base)) {
    response.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    response.end('404: not part of this site\n');
    return;
  }
  const relative = path.slice(base.length);
  if (/\\|\0/.test(relative) || relative.split('/').some((p) => p === '..' || p === '.')) {
    notFound(request, response);
    return;
  }
  const direct = file(
    relative === '' || relative.endsWith('/') ? `${relative}index.html` : relative,
  );
  if (direct === 'directory') {
    response.writeHead(301, { Location: `${path}/${url.search}` });
    response.end();
  } else if (direct !== null) {
    send(request, response, 200, direct);
  } else {
    const page = relative.endsWith('/') ? null : file(`${relative}.html`);
    if (page !== null && page !== 'directory') send(request, response, 200, page);
    else notFound(request, response);
  }
});

server.listen(Number(values.port), values.host, () => {
  console.log(`static build ${root} at http://${values.host}:${values.port}${base}`);
});
for (const signal of ['SIGINT', 'SIGTERM'])
  process.on(signal, () => server.close(() => process.exit(0)));
