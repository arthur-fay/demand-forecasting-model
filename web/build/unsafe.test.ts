/** (M9) The scan of a published file (scripts/unsafe.mjs): every pattern, and no false alarm. */
import { readdirSync, readFileSync } from 'node:fs';
import { extname, join } from 'node:path';
import { describe, expect, it } from 'vitest';

import { ALLOWED_HOSTS, BINARY_EXTENSIONS, findUnsafe } from '../scripts/unsafe.mjs';

const found = (text: string) => findUnsafe(text).map((f) => f.split(':')[0]);

describe('what a published file must not hold', () => {
  it.each([
    ['-----BEGIN OPENSSH PRIVATE KEY-----', 'a private key'],
    ['ghp_abcdefghijklmnopqrstuvwxyz0123456789', 'a GitHub token'],
    ['github_pat_11ABCDEFG0123456789_abcdefghijklmnopqrstuv', 'a GitHub token'],
    ['AKIAIOSFODNN7EXAMPLE', 'an AWS key'],
    ['xoxb-1234567890-abcdefghij', 'a Slack token'],
    ['sk-abcdefghijklmnopqrstuvwx', 'an API key value'],
    ['eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig', 'a JSON web token'],
    ['password: "hunter22hunter22"', 'a credential assignment'],
    ['Authorization: Bearer abc', 'an authorization header'],
    ['Bearer 0123456789abcdefABCDEF', 'a bearer or basic credential'],
    ['headers["X-API-Key"]', 'an API key header'],
    ['https://alice:secret@example.org/', 'a URL with credentials'],
    ['file:///home/arthur/atlas', 'a file URL'],
    ['written to /home/arthur/atlas', 'a local path'],
    ['/var/lib/atlas', 'a local path'],
    ['/opt/atlas/releases', 'a local path'],
    ['/__w/demand-forecasting-model', 'a local path'],
    ['C:\\Users\\alice', 'a Windows path'],
    ['C:/Users/alice', 'a Windows path'],
    ['\\\\fileserver\\share', 'a network share'],
    ['http://localhost:8000', 'a local host'],
    ['127.0.0.1', 'a local host'],
    ['[::1]:8000', 'a local host'],
    ['build-07.corp.internal', 'an internal host name'],
    ['printer.local', 'an internal host name'],
    ['10.0.3.17', 'an IP address'],
    ['8.8.8.8', 'an IP address'],
    ['2001:0db8:85a3:0000:0000:8a2e:0370:7334', 'an IPv6 address'],
    ['owner@example.org', 'an e-mail address'],
    ['https://example.org/tracker.js', 'a link to another site'],
    // Second review round: forms the first patterns missed.
    ['<script src="//evil.example/x.js">', 'a link to another site'],
    ['.x{background:url(//evil.example/p.png)}', 'a link to another site'],
    ["@import '//evil.example/a.css';", 'a link to another site'],
    ['const REPOSITORY = "//evil.example/arthur-fay/x";', 'a link to another site'],
    ['href="\\\\evil.example/x"', 'a link to another site'],
    ['"https:evil.example/x"', 'a link to another site'],
    ['"https:/evil.example/x"', 'a link to another site'],
    ['"https:///evil.example/"', 'a link to another site'],
    ['"https://\tevil.example/"', 'a link to another site'],
    ['ssh://evil.example', 'a link to another site'],
    ['sftp://files.example.org/x', 'a link to another site'],
    ['fe80::1ff:fe23:4567:890a', 'an IPv6 address'],
    ['2001:db8:85a3::8a2e:370:7334', 'an IPv6 address'],
    ['2001:db8::abcd', 'an IPv6 address'],
    ['fd00::1234:5678', 'an IPv6 address'],
    ['D:\\data\\atlas', 'a Windows path'],
    ['D:/projects/atlas', 'a Windows path'],
    ['LOCALHOST', 'a local host'],
    ['token=abcdefgh1234', 'a credential assignment'],
    ['api_key: AbCdEfGhIjKlMnOp', 'a credential assignment'],
    ['redis://:password@cache.example.org:6379', 'a URL with credentials'],
    // Third check: forms the second round's patterns missed (each loads a foreign host).
    ['<script src="///evil.example/x.js">', 'a link to another site'],
    ['import("\\/\\/evil.example/x.js")', 'a link to another site'],
    ['<img src="/\t/evil.example/f.png">', 'a link to another site'],
    ['<img src="//@evil.example/e.png">', 'a link to another site'],
    ['<img srcset="a.png 1x, //evil.example/b.png 2x">', 'a link to another site'],
    ['<meta http-equiv="refresh" content="0;url=//evil.example">', 'a link to another site'],
    ['<a ping=//evil.example/p href="#">', 'a link to another site'],
    ['"http:evil.example/x"', 'a link to another site'],
    ['"wss:evil.example"', 'a link to another site'],
    ['https:\\\\evil.example/x', 'a link to another site'],
    ['The server is 2001:db8::abcd.', 'an IPv6 address'],
    ['listening on ::1 port 8000', 'a local host'],
    ['SECRET_KEY = "wJalrXUtnFEMIK7MDENGbPxRfiCYEXAMPLEKEY"', 'a credential assignment'],
    ['aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY', 'a credential assignment'],
    ['client_secret_value: "abcdefgh1234"', 'a credential assignment'],
    ['D:\\atlas', 'a Windows path'],
    ['C:\\secrets.txt', 'a Windows path'],
    ['"D:\\\\atlas"', 'a Windows path'],
  ])('%s is %s', (text, what) => {
    expect(found(text)).toContain(what);
  });

  it('allows what the build legitimately names', () => {
    for (const text of [
      'http://www.w3.org/2000/svg',
      'https://react.dev/errors/418',
      'https://github.com/arthur-fay/demand-forecasting-model/blob/main/docs',
      'http://scripts.sil.org/OFL',
      'requests/day; 1.0.0; 2026-06-30; 3.12.15; data/history-365d.json',
      'Basic Authentication understanding of the basic bearer of costs',
      'a:b:c?d:e; x ? 1:2:3',
      '/api/v1/forecasts/history?start=2023-01-01',
      // Not addresses or paths: CSS pseudo-elements, times, ratios, code.
      'a::before, a::after, ::selection, ::-webkit-scrollbar { x: 1 }',
      'at 12:30:00, 1:2 and 10:20:30:40',
      '{token:e.value,secret:t,password:n}',
      'protocol==="https:"&&x; e=/^https?:\\/\\//',
      "'//' + path; s.split('//')",
      // Third check: regex literals after a property name, object keys named after schemes,
      // prose, expressions, JS escapes and short hex runs.
      '{test:/\\.ts$/,id:/\\.[jt]sx?$/}',
      'a?b:/\\d+/.test(n)',
      '{path:/\\//g,evaluate:/\\{\\{/g,pathname:/\\/$/}',
      '/^https:\\/\\//i.test(e)',
      '{http:t.http,https:t.https}; ws:e.ws',
      'Note: // see below; served over https: github.io pages',
      'token=createTokenizer(e); {tokenType:e.type}',
      'secret: understanding the model',
      'A:\\n\\tB',
      'a::b, 12::30, C::9bb',
    ])
      expect(findUnsafe(text), text).toEqual([]);
    expect(ALLOWED_HOSTS).toEqual(['www.w3.org', 'react.dev', 'github.com', 'scripts.sil.org']);
  });

  it('finds nothing in the committed build and export', () => {
    const roots = ['dist', 'static-export'].map((d) => join(import.meta.dirname, '..', d));
    const walk = (dir: string): string[] =>
      readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
        e.isDirectory() ? walk(join(dir, e.name)) : [join(dir, e.name)],
      );
    for (const file of roots.flatMap(walk)) {
      if (BINARY_EXTENSIONS.includes(extname(file))) continue;
      expect(findUnsafe(readFileSync(file, 'utf-8')), file).toEqual([]);
    }
  });
});
