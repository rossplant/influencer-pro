"""Staging UAT journey. Creates a set and does not send email unless --send is set.

Environment:
  UAT_E2E_BASE_URL
  STAGING_TEST_ADMIN_EMAIL or UAT_E2E_ADMIN_EMAIL
  STAGING_TEST_ADMIN_PASSWORD or UAT_E2E_ADMIN_PASSWORD

Invite paths are followed and never printed. The password is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone


def _cred(primary: str, fallback: str) -> str:
    return os.getenv(primary, "").strip() or os.getenv(fallback, "").strip()


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


def _json_login(base: str, email: str, password: str) -> str:
    request = urllib.request.Request(
        f"{base}/admin/login",
        data=json.dumps({"email": email, "password": password}).encode(),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            cookie = response.headers.get("set-cookie", "").split(";", 1)[0]
    except urllib.error.HTTPError as exc:
        print(f"Admin sign-in failed ({exc.code})")
        sys.exit(1)
    if status not in {200, 303} or not cookie:
        print(f"Admin sign-in failed ({status})")
        sys.exit(1)
    return cookie


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Create a staging UAT set without printing secrets.")
    parser.add_argument("--first-name", default="")
    parser.add_argument("--brand", default="")
    parser.add_argument("--creatr-email", default="")
    parser.add_argument("--send", action="store_true", help="Press Send invite. Off unless this flag is set.")
    args = parser.parse_args(argv)
    base = os.getenv("UAT_E2E_BASE_URL", "").strip().rstrip("/")
    if not base:
        sys.exit("Missing UAT_E2E_BASE_URL")
    if not base.startswith("https://"):
        sys.exit("UAT_E2E_BASE_URL must be https")
    email = _cred("STAGING_TEST_ADMIN_EMAIL", "UAT_E2E_ADMIN_EMAIL")
    password = _cred("STAGING_TEST_ADMIN_PASSWORD", "UAT_E2E_ADMIN_PASSWORD")
    if not email or not password:
        sys.exit("Missing STAGING_TEST_ADMIN_EMAIL or UAT_E2E_ADMIN_EMAIL")
    cookie = _json_login(base, email, password)
    auth = {"Cookie": cookie}
    status, _, page = _request(f"{base}/admin/uat", headers=auth)
    if status != 200 or "Create set (no email)" not in page:
        sys.exit("UAT page did not load")
    if "Ross at Plantr Collab" not in page or "hello@plantr-collab.com" not in page:
        sys.exit("The From name or Reply-To was not shown")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    target = f"uat-e2e+{stamp}@example.com"
    status, _, created = _request(
        f"{base}/admin/uat/create-set",
        data={
            "csrf_token": _hidden(page, "csrf_token"),
            "email": target,
            "first_name": args.first_name,
            "brand_name": args.brand,
            "creatr_email": args.creatr_email,
        },
        headers=auth,
        method="POST",
    )
    if status != 200 or "No email was sent" not in created:
        sys.exit("Create set sent mail or failed")
    if "Resend id" in created:
        sys.exit("An invite email was sent")
    if created.count("not sent yet") < 2:
        sys.exit("The page did not show a separate status for each invite")
    if not args.send:
        print(f"UAT set created for uat-e2e+{stamp}@example.com with no email")
        return
    status, _, sent = _request(
        f"{base}/admin/uat/send",
        data={
            "csrf_token": _hidden(created, "csrf_token"),
            "email": target,
            "press_id": _hidden(created, "press_id"),
        },
        headers=auth,
        method="POST",
    )
    if status != 200 or "invite failed" in sent.lower():
        print("Send invite failed")
        sys.exit(1)
    print(f"Send invite pressed for uat-e2e+{stamp}@example.com")


if __name__ == "__main__":
    main()
