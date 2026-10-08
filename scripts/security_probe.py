#!/usr/bin/env python
"""Black-box HTTP probe of a running portal (no credentials needed). Standard library only.

Usage: python scripts/security_probe.py http://127.0.0.1:8000
Checks response headers, method handling, error pages, sensitive paths, the login form's CSRF and
throttling behaviour. Exit status 1 if any check fails. Run against staging, never production
during business hours (it makes a few failed sign-ins).
"""

from __future__ import annotations

import http.cookiejar
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def opener(cookies=None):
    handlers = [NoRedirect()]
    if cookies is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(cookies))
    return urllib.request.build_opener(*handlers)


def fetch(base, path, method="GET", data=None, headers=None, cookies=None):
    # base is checked to be http(s) in main()
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers or {})  # noqa: S310
    try:
        response = opener(cookies).open(request, timeout=15)  # noqa: S310
        return response.status, response.headers, response.read()  # case-insensitive lookups
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def main(base: str) -> int:
    base = base.rstrip("/")
    if urllib.parse.urlsplit(base).scheme not in ("http", "https"):
        print("base URL must be http:// or https://")
        return 2
    status, headers, body = fetch(base, "/accounts/login/")
    check("login page reachable", status == 200, str(status))
    csp = headers.get("Content-Security-Policy", "")
    check("CSP present without unsafe-inline/eval", bool(csp) and "unsafe-inline" not in csp and "unsafe-eval" not in csp,
          csp[:80])
    check("frame-ancestors none", "frame-ancestors 'none'" in csp)
    check("X-Frame-Options DENY", headers.get("X-Frame-Options") == "DENY")
    check("nosniff", headers.get("X-Content-Type-Options") == "nosniff")
    check("Referrer-Policy", headers.get("Referrer-Policy") in ("same-origin", "strict-origin-when-cross-origin",
                                                                  "no-referrer"))
    check("no Server version leak", not re.search(r"\d+\.\d+", headers.get("Server", "")), headers.get("Server", ""))
    check("no inline script in login page", b"<script>" not in body and b"onclick=" not in body)

    status, headers, _ = fetch(base, "/")
    check("dashboard requires sign-in", status == 302 and "/accounts/login/" in headers.get("Location", ""), str(status))

    for path in ("/.env", "/.git/config", "/django-admin/", "/admin/", "/static/../manage.py", "/media/", "/backup.sql",
                 "/settings.py"):
        status, _, body = fetch(base, path)
        check(f"not served: {path}", status in (302, 400, 404) and b"SECRET" not in body, str(status))

    status, _, body = fetch(base, "/this-page-does-not-exist-404")
    check("404 page without debug detail", status == 404 and b"Traceback" not in body and b"URLconf" not in body,
          str(status))

    status, _, _ = fetch(base, "/accounts/logout/")
    check("logout refuses GET", status == 405, str(status))
    status, _, _ = fetch(base, "/healthz", method="POST")
    check("health check refuses POST", status in (403, 405), str(status))
    status, _, _ = fetch(base, "/accounts/login/", method="TRACE")
    check("TRACE refused", status in (400, 403, 405, 501), str(status))

    data = urllib.parse.urlencode({"identifier": "probe-user", "password": "probe-password"}).encode()
    status, _, body = fetch(base, "/accounts/login/", method="POST", data=data,
                            headers={"Content-Type": "application/x-www-form-urlencoded"})
    check("login POST without CSRF token refused", status == 403, str(status))

    jar = http.cookiejar.CookieJar()
    _, _, page = fetch(base, "/accounts/login/", cookies=jar)
    token_match = re.search(rb'name="csrfmiddlewaretoken" value="([^"]+)"', page)
    token = token_match.group(1).decode() if token_match else ""
    statuses, first_body = [], b""
    for _ in range(7):
        data = urllib.parse.urlencode({"identifier": "probe-nonexistent", "password": "wrong-password-123",
                                       "csrfmiddlewaretoken": token}).encode()
        status, _, body = fetch(base, "/accounts/login/", method="POST", data=data, cookies=jar,
                                headers={"Content-Type": "application/x-www-form-urlencoded",
                                         "Referer": base + "/accounts/login/"})
        statuses.append(status)
        first_body = first_body or body
    check("generic failure for unknown account", statuses[0] == 200 and b"Invalid credentials" in first_body,
          str(statuses[0]))
    check("throttling kicks in after repeated failures", 429 in statuses, str(statuses))

    status, headers, body = fetch(base, "/api/v1/me/")
    check("API refuses anonymous", status == 403 and b"Traceback" not in body, str(status))
    status, headers, _ = fetch(base, "/healthz", headers={"X-Request-ID": "<script>"})
    check("request id sanitised", "<" not in headers.get("X-Request-ID", "<"), headers.get("X-Request-ID", ""))

    failed = [r for r in RESULTS if not r[1]]
    for name, ok, detail in RESULTS:
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  [' + detail + ']') if detail else ''}")
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
