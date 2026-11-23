"""Staging UAT journey. Creates a set and does not send email.

Environment:
  UAT_E2E_BASE_URL
  UAT_E2E_ADMIN_EMAIL
  UAT_E2E_ADMIN_PASSWORD

Invite paths are followed and never printed.
"""

from __future__ import annotations

import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        sys.exit(f"Missing {name}")
    return value


def _request(url: str, *, data: dict | None = None, headers: dict | None = None, method: str = "GET"):
    body = None
    req_headers = {"Accept": "text/html", **(headers or {})}
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        req_headers["Content-Type"] = "application/x-www-form-urlencoded"
    request = urllib.request.Request(url, data=body, headers=req_headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, dict(response.headers), response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read().decode()


def _hidden(html: str, name: str) -> str:
    marker = f'name="{name}" value="'
    start = html.find(marker)
    if start < 0:
        return ""
    start += len(marker)
    end = html.find('"', start)
    return html[start:end]


def main() -> None:
    base = _env("UAT_E2E_BASE_URL").rstrip("/")
    if not base.startswith("https://"):
        sys.exit("UAT_E2E_BASE_URL must be https")
    email = _env("UAT_E2E_ADMIN_EMAIL")
    password = _env("UAT_E2E_ADMIN_PASSWORD")
    status, headers, page = _request(f"{base}/admin/login", data={"email": email, "password": password}, method="POST")
    if status not in {200, 303} or "set-cookie" not in {key.lower() for key in headers}:
        # JSON login.
        request = urllib.request.Request(
            f"{base}/admin/login",
            data=__import__("json").dumps({"email": email, "password": password}).encode(),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            cookie = response.headers.get("set-cookie", "").split(";", 1)[0]
            status = response.status
    else:
        cookie = headers.get("set-cookie", headers.get("Set-Cookie", "")).split(";", 1)[0]
    if status not in {200, 303} or not cookie:
        sys.exit("Admin sign-in failed")
    auth = {"Cookie": cookie}
    status, _, page = _request(f"{base}/admin/uat", headers=auth)
    if status != 200 or "Create set (no email)" not in page:
        sys.exit("UAT page did not load")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = f"uat-e2e+{stamp}@example.com"
    status, _, created = _request(
        f"{base}/admin/uat/create-set",
        data={"csrf_token": _hidden(page, "csrf_token"), "email": target},
        headers=auth,
        method="POST",
    )
    if status != 200 or "No email was sent" not in created:
        sys.exit("Create set sent mail or failed")
    if "Invite sent" in created:
        sys.exit("An invite email was sent")
    print(f"UAT set created for uat-e2e+{stamp}@example.com with no email")


if __name__ == "__main__":
    main()
