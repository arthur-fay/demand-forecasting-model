"""The interactive documentation pages: ``/docs`` (Swagger UI) and ``/redoc`` (ReDoc).

FastAPI's own pages load whatever jsDelivr serves for the latest major version of each viewer
(``swagger-ui-dist@5``, ``redoc@2``), without Subresource Integrity, and the API serves them
without a Content-Security-Policy. They share the origin of the API and the dashboard, and
Swagger UI's "Authorize" dialog takes the simulation key. These pages therefore load one
exact version of each file, with its SRI hash, so that the browser refuses a file that has
changed. Each page has its own policy: scripts only from its pinned file (and Swagger UI's
initialisation script, by its hash); styles only from the pinned stylesheet, except that
ReDoc writes its styles into the page at run time; connections only to the API itself. No
web font, favicon or online validator is requested, and the policy refuses ReDoc's footer
logo (cdn.redoc.ly).

The SRI values are the SHA-384 of the files in the npm registry tarballs of these versions,
each tarball first checked against the registry's own ``dist.integrity``, for example::

    curl -sO https://registry.npmjs.org/swagger-ui-dist/-/swagger-ui-dist-5.33.1.tgz
    tar -xzOf swagger-ui-dist-5.33.1.tgz package/swagger-ui-bundle.js \\
      | openssl dgst -sha384 -binary | openssl base64 -A

To move to another version, choose one published at least two weeks earlier (the project's
dependency cooldown), recompute the values, update ``tests/test_m5_security.py`` and check
both pages in a browser.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Final

from fastapi import FastAPI
from starlette.requests import Request
from starlette.responses import HTMLResponse

#: swagger-ui-dist 5.33.1 (published 2026-10-01) and redoc 2.5.4 (published 2026-09-10).
SWAGGER_UI: Final = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.33.1/"
REDOC: Final = "https://cdn.jsdelivr.net/npm/redoc@2.5.4/bundles/"


@dataclass(frozen=True)
class Asset:
    """One third-party file: its exact URL and its Subresource Integrity value."""

    url: str
    integrity: str

    def attributes(self, name: str) -> str:
        return f'{name}="{self.url}" integrity="{self.integrity}" crossorigin="anonymous"'


SWAGGER_JS: Final = Asset(
    SWAGGER_UI + "swagger-ui-bundle.js",
    "sha384-ZPehFMQommnnuaZ4rpxgkgTT2DKFVp4hZC/7pLit+9Lek9T1YGSo23eHFbvNkXkw",
)
SWAGGER_CSS: Final = Asset(
    SWAGGER_UI + "swagger-ui.css",
    "sha384-Ov4/wv3j2bmct8cDc5X4ngJZohVPzEmc6uDPH8WeljUxO5vtoykvMEfbu9Vh6RaW",
)
REDOC_JS: Final = Asset(
    REDOC + "redoc.standalone.js",
    "sha384-w447zOpYfw/1Tv/5AK9NfHTlQIqE3RVR6KY62jCyy9zNDgO64cMwGGP1Fj0zJVf5",
)

_HEAD: Final = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<link rel="icon" href="data:,">
"""


def _inline_hash(script: str) -> str:
    """The CSP source that allows exactly this inline script."""
    return "'sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode() + "'"


def _policy(*directives: str) -> str:
    """A page policy: nothing by default, never framed, no base URL or form target."""
    fixed = ("base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'")
    return "; ".join(("default-src 'none'", *directives, *fixed))


@dataclass(frozen=True)
class Page:
    """One documentation page and the Content-Security-Policy it is served with."""

    html: str
    policy: str

    def response(self) -> HTMLResponse:
        return HTMLResponse(self.html, headers={"Content-Security-Policy": self.policy})


def swagger_page(title: str, openapi_url: str) -> Page:
    # FastAPI's Swagger UI settings, without the OAuth2 redirect (the API has no OAuth2
    # flow) and without the online validator (it would send the document's URL away).
    init = (
        "SwaggerUIBundle({"
        f'url: "{openapi_url}", dom_id: "#swagger-ui", layout: "BaseLayout", '
        "deepLinking: true, showExtensions: true, showCommonExtensions: true, "
        "validatorUrl: null, "
        "presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset]"
        "});"
    )
    html = (
        _HEAD.format(title=f"{title} - Swagger UI")
        + f'<link rel="stylesheet" {SWAGGER_CSS.attributes("href")}>\n'
        + '</head>\n<body>\n<div id="swagger-ui"></div>\n'
        + f"<script {SWAGGER_JS.attributes('src')}></script>\n"
        + f"<script>{init}</script>\n</body>\n</html>\n"
    )
    policy = _policy(
        f"script-src {SWAGGER_JS.url} {_inline_hash(init)}",
        f"style-src {SWAGGER_CSS.url}",
        "img-src data:",
        "connect-src 'self'",
    )
    return Page(html, policy)


def redoc_page(title: str, openapi_url: str) -> Page:
    html = (
        _HEAD.format(title=f"{title} - ReDoc")
        + "<style>body { margin: 0; padding: 0; }</style>\n</head>\n<body>\n"
        + "<noscript>ReDoc needs JavaScript to show this documentation.</noscript>\n"
        + f'<redoc spec-url="{openapi_url}"></redoc>\n'
        + f"<script {REDOC_JS.attributes('src')}></script>\n</body>\n</html>\n"
    )
    # ReDoc writes its styles into the page at run time and searches in a worker that it
    # starts from a blob: URL.
    policy = _policy(
        f"script-src {REDOC_JS.url}",
        "style-src 'unsafe-inline'",
        "img-src data:",
        "connect-src 'self'",
        "worker-src blob:",
    )
    return Page(html, policy)


def install_docs(app: FastAPI, openapi_url: str) -> None:
    """Serve ``/docs`` and ``/redoc`` (GET and HEAD) for ``app``'s OpenAPI document."""
    swagger, redoc = swagger_page(app.title, openapi_url), redoc_page(app.title, openapi_url)

    async def swagger_ui(request: Request) -> HTMLResponse:
        return swagger.response()

    async def redoc_ui(request: Request) -> HTMLResponse:
        return redoc.response()

    app.add_route("/docs", swagger_ui, include_in_schema=False)
    app.add_route("/redoc", redoc_ui, include_in_schema=False)
