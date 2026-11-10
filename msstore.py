#!/usr/bin/env python3
"""Talks to the Microsoft Store submission API from GitHub Actions.

Signs in without a client secret: GitHub's OIDC token for the job is
exchanged for an Entra ID token of the app registered in Partner Center
(a federated credential on that app trusts the calling repository's
`msstore` environment). Only the Python standard library is used.

Commands:
  check   Reads the app and its submissions (changes nothing): checks that
          signing in works and shows the state of the app.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# The Partner Center account's Entra ID tenant and the app registered in it
# (not secret: they only say who signs in; the federated credential decides
# which repositories may).
TENANT_ID = "cff454cd-86b8-45f7-9ae2-b4bbe1037267"
CLIENT_ID = "bbef9864-3d4c-4937-9f29-3137ec2af5ab"
# The Partner Center account (seller), for the newer API.
SELLER_ID = "97221490"

# The submission API for MSIX apps.
API = "https://manage.devcenter.microsoft.com/v1.0/my"
SCOPE = "https://manage.devcenter.microsoft.com/.default"


def request(
    method: str,
    url: str,
    token: str | None = None,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    """Sends a request and returns the JSON answer (None if empty)."""
    all_headers = dict(headers or {})
    if token:
        all_headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method=method, headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as answer:
            data = answer.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        raise SystemExit(f"{method} {url}: HTTP {err.code}\n{detail}") from None
    return json.loads(data) if data else None


def mask(value: str) -> None:
    """Keeps a value out of the GitHub Actions log."""
    if os.environ.get("GITHUB_ACTIONS"):
        print(f"::add-mask::{value}", flush=True)


def github_oidc_token() -> str:
    """The job's OIDC token from GitHub, for Entra ID's token exchange."""
    url = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_URL")
    request_token = os.environ.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN")
    if not url or not request_token:
        raise SystemExit(
            "no OIDC token: run in GitHub Actions with `permissions: id-token: write`"
        )
    audience = urllib.parse.quote("api://AzureADTokenExchange", safe="")
    answer = request("GET", f"{url}&audience={audience}", token=request_token)
    token = answer["value"]
    mask(token)
    return token


def store_token() -> str:
    """An access token for the submission API, from the GitHub OIDC token."""
    form = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "grant_type": "client_credentials",
            "scope": SCOPE,
            "client_assertion_type": "urn:ietf:params:oauth:client-assertion-type:jwt-bearer",
            "client_assertion": github_oidc_token(),
        }
    ).encode()
    answer = request(
        "POST",
        f"https://login.microsoftonline.com/{TENANT_ID}/oauth2/v2.0/token",
        body=form,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    token = answer["access_token"]
    mask(token)
    return token


def check(app_id: str) -> None:
    """Shows the app and its submissions; changes nothing."""
    token = store_token()
    print("signed in to the Microsoft Store submission API")
    app = request("GET", f"{API}/applications/{app_id}", token=token)
    print(f"app: {app.get('primaryName')} ({app_id})")
    for key in ("lastPublishedApplicationSubmission", "pendingApplicationSubmission"):
        ref = app.get(key)
        if not ref:
            print(f"{key}: none")
            continue
        sub = request("GET", f"{API}/applications/{app_id}/submissions/{ref['id']}", token=token)
        packages = [p.get("fileName") for p in sub.get("applicationPackages", [])]
        print(
            f"{key}: {ref['id']}, status {sub.get('status')}, "
            f"listings {sorted(sub.get('listings', {}))}, packages {packages}"
        )


def main() -> None:
    """Parses the command line and runs the command."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_check = sub.add_parser("check", help="read the app and its submissions (changes nothing)")
    p_check.add_argument("--app-id", required=True, help="the Store ID, such as 9P6KDQX7JZS4")
    args = parser.parse_args()
    if args.command == "check":
        check(args.app_id)


if __name__ == "__main__":
    sys.exit(main())
