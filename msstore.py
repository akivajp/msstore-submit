#!/usr/bin/env python3
"""Talks to the Microsoft Store submission API from GitHub Actions.

Signs in without a client secret: GitHub's OIDC token for the job is
exchanged for an Entra ID token of the app registered in Partner Center
(a federated credential on that app trusts the calling repository's
`msstore` environment). Only the Python standard library is used.

Commands:
  check    Reads the app and its submissions (changes nothing).
  show     Prints the last published (or the pending) submission as JSON,
           without its upload URL (changes nothing).
  submit   Makes a new submission from the last published one: replaces
           the packages (--package, once per architecture's package),
           applies the listing files (printing what changes),
           uploads the files, and commits it, then follows it until
           certification starts. With --no-commit it stays a draft: Partner
           Center shows its changes only once committed (and a submission
           edited there can no longer be changed through the API), so look
           at the printed changes, then use the commit command.
  commit   Commits the pending submission (a draft left by submit
           --no-commit, looked at in Partner Center) and follows it.
  discard  Deletes the pending submission (a draft or a failed one).

Listing files (one TOML file per language, named after it: en-us.toml,
ja-jp.toml, ...) hold the fields to set; the ones left out keep the values
of the last submission:

  title = "FlexFlyout"
  short_description = "..."
  description = '''...'''
  release_notes = "..."          # "What's new in this version"
  features = ["...", "..."]
  keywords = ["...", "..."]      # search terms, 7 at most
  copyright = "Copyright (c) 2026 Akiva Miura"
  license_terms = "..."
  developed_by = "Akiva Miura"
  [[screenshots]]                # replaces all screenshots, with --images
  file = "screenshots/en-1.png"  # relative to the listing file
  caption = "..."
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
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

# Listing file fields and the submission's baseListing keys they set.
TEXT_FIELDS = {
    "title": "title",
    "short_description": "shortDescription",
    "description": "description",
    "release_notes": "releaseNotes",
    "copyright": "copyrightAndTrademarkInfo",
    "license_terms": "licenseTerms",
    "developed_by": "devStudio",
}
LIST_FIELDS = {"features": "features", "keywords": "keywords"}

# How long to follow a commit, and how often to look.
COMMIT_TIMEOUT = 30 * 60
POLL_INTERVAL = 20


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
        with urllib.request.urlopen(req, timeout=300) as answer:
            data = answer.read()
    except urllib.error.HTTPError as err:
        detail = err.read().decode("utf-8", "replace")
        # Never print a URL with a key in it (the upload URL).
        shown = url.split("?", 1)[0]
        raise SystemExit(f"{method} {shown}: HTTP {err.code}\n{detail}") from None
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return data.decode("utf-8", "replace")


def json_body(value: Any) -> tuple[bytes, dict[str, str]]:
    """A JSON request body and its header."""
    return json.dumps(value).encode(), {"Content-Type": "application/json"}


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
    print("signed in to the Microsoft Store submission API", flush=True)
    return token


def app_info(token: str, app_id: str) -> dict[str, Any]:
    """The app, with the ids of its last published and pending submissions."""
    return request("GET", f"{API}/applications/{app_id}", token=token)


def get_submission(token: str, app_id: str, submission_id: str) -> dict[str, Any]:
    """One submission."""
    return request("GET", f"{API}/applications/{app_id}/submissions/{submission_id}", token=token)


def check(app_id: str) -> None:
    """Shows the app and its submissions; changes nothing."""
    token = store_token()
    app = app_info(token, app_id)
    print(f"app: {app.get('primaryName')} ({app_id})")
    for key in ("lastPublishedApplicationSubmission", "pendingApplicationSubmission"):
        ref = app.get(key)
        if not ref:
            print(f"{key}: none")
            continue
        sub = get_submission(token, app_id, ref["id"])
        packages = [p.get("fileName") for p in sub.get("applicationPackages", [])]
        print(
            f"{key}: {ref['id']}, status {sub.get('status')}, "
            f"listings {sorted(sub.get('listings', {}))}, packages {packages}"
        )


def show(app_id: str) -> None:
    """Prints the pending (else the last published) submission as JSON."""
    token = store_token()
    app = app_info(token, app_id)
    ref = app.get("pendingApplicationSubmission") or app.get("lastPublishedApplicationSubmission")
    if not ref:
        raise SystemExit("the app has no submission yet")
    sub = get_submission(token, app_id, ref["id"])
    sub.pop("fileUploadUrl", None)
    print(json.dumps(sub, indent=2, ensure_ascii=False))


def load_listings(folder: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    """The listing files in `folder`, by language ("en-us": (fields, file))."""
    listings = {}
    for path in sorted(folder.glob("*.toml")):
        with path.open("rb") as file:
            listings[path.stem.lower()] = (tomllib.load(file), path)
    if not listings:
        raise SystemExit(f"no listing files (*.toml) in {folder}")
    return listings


def apply_listing(
    base: dict[str, Any],
    fields: dict[str, Any],
    source: Path,
    images: bool,
    files: dict[str, Path],
) -> list[str]:
    """Sets one language's fields on its baseListing; returns what changed.

    With `images`, the screenshots of the listing file replace the old
    ones, and the files to upload are added to `files` (name in the
    zip -> file on disk).
    """
    changed = []
    for field, key in TEXT_FIELDS.items():
        if field in fields and base.get(key) != fields[field]:
            base[key] = fields[field]
            changed.append(key)
            print(f"--- {source.stem} {key}:\n{fields[field]}\n", flush=True)
    for field, key in LIST_FIELDS.items():
        if field in fields and list(base.get(key) or []) != list(fields[field]):
            base[key] = list(fields[field])
            changed.append(key)
            items = "\n".join(f"- {item}" for item in fields[field])
            print(f"--- {source.stem} {key}:\n{items}\n", flush=True)
    shots = fields.get("screenshots")
    if images and shots:
        kept = []
        for image in base.get("images", []):
            if image.get("imageType") == "Screenshot":
                image["fileStatus"] = "PendingDelete"
            kept.append(image)
        for shot in shots:
            path = (source.parent / shot["file"]).resolve()
            if not path.is_file():
                raise SystemExit(f"{source}: no screenshot {shot['file']}")
            name = f"{source.stem}/{path.name}"
            files[name] = path
            kept.append(
                {
                    "fileName": name,
                    "fileStatus": "PendingUpload",
                    "imageType": "Screenshot",
                    "description": shot.get("caption", ""),
                }
            )
        base["images"] = kept
        changed.append(f"images ({len(shots)} screenshots)")
    return changed


def upload(url: str, files: dict[str, Path]) -> None:
    """Zips the files under their names and puts the zip at the upload URL."""
    mask(url)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, path in files.items():
            archive.write(path, name)
    data = buffer.getvalue()
    print(f"uploading {len(files)} files ({len(data) / 1e6:.1f} MB)", flush=True)
    request("PUT", url, body=data, headers={"x-ms-blob-type": "BlockBlob"})


def follow(token: str, app_id: str, submission_id: str) -> None:
    """Follows a committed submission until the commit is processed."""
    deadline = time.monotonic() + COMMIT_TIMEOUT
    while True:
        status = request(
            "GET", f"{API}/applications/{app_id}/submissions/{submission_id}/status", token=token
        )
        state = status.get("status")
        print(f"status: {state}", flush=True)
        if state == "CommitFailed":
            details = json.dumps(status.get("statusDetails"), indent=2, ensure_ascii=False)
            raise SystemExit(f"the commit failed:\n{details}")
        if state != "CommitStarted":
            return
        if time.monotonic() > deadline:
            raise SystemExit("the commit is still being processed; see Partner Center")
        time.sleep(POLL_INTERVAL)


def submit(
    app_id: str,
    packages: list[Path],
    listing_dir: Path | None,
    images: bool,
    commit: bool,
) -> None:
    """Makes, fills and (unless told not to) commits a new submission."""
    token = store_token()
    app = app_info(token, app_id)
    pending = app.get("pendingApplicationSubmission")
    if pending:
        raise SystemExit(
            f"the app has a pending submission ({pending['id']}): finish it in Partner Center, "
            "or delete it with the discard command"
        )
    sub = request("POST", f"{API}/applications/{app_id}/submissions", token=token)
    submission_id = sub["id"]
    upload_url = sub.pop("fileUploadUrl")
    mask(upload_url)
    print(f"new submission {submission_id}", flush=True)
    files: dict[str, Path] = {}

    if packages:
        # The new packages replace every package of the last submission.
        for old in sub.get("applicationPackages", []):
            old["fileStatus"] = "PendingDelete"
    for package in packages:
        sub.setdefault("applicationPackages", []).append(
            {
                "fileName": package.name,
                "fileStatus": "PendingUpload",
                "minimumDirectXVersion": "None",
                "minimumSystemRam": "None",
            }
        )
        files[package.name] = package
        print(f"package: {package.name}")

    if listing_dir:
        listings = sub.setdefault("listings", {})
        for language, (fields, source) in load_listings(listing_dir).items():
            entry = listings.setdefault(language, {"baseListing": {}, "platformOverrides": {}})
            changed = apply_listing(entry.setdefault("baseListing", {}), fields, source, images, files)
            print(f"listing {language}: {', '.join(changed) or 'no changes'}")

    body, headers = json_body(sub)
    request(
        "PUT", f"{API}/applications/{app_id}/submissions/{submission_id}",
        token=token, body=body, headers=headers,
    )
    if files:
        upload(upload_url, files)
    if not commit:
        print(
            "left as a draft. Partner Center shows an API submission's changes only once it "
            "is committed, and one edited or submitted there can no longer be changed through "
            "the API: look at the changes above (or with the show command), then commit it "
            "with the commit command, or delete it with the discard command"
        )
        return
    request("POST", f"{API}/applications/{app_id}/submissions/{submission_id}/commit", token=token)
    print("committed", flush=True)
    follow(token, app_id, submission_id)


def commit_pending(app_id: str) -> None:
    """Commits the pending submission (a draft) and follows it."""
    token = store_token()
    pending = app_info(token, app_id).get("pendingApplicationSubmission")
    if not pending:
        raise SystemExit("no pending submission")
    request("POST", f"{API}/applications/{app_id}/submissions/{pending['id']}/commit", token=token)
    print(f"committed the pending submission {pending['id']}", flush=True)
    follow(token, app_id, pending["id"])


def discard(app_id: str) -> None:
    """Deletes the pending submission."""
    token = store_token()
    pending = app_info(token, app_id).get("pendingApplicationSubmission")
    if not pending:
        print("no pending submission")
        return
    request("DELETE", f"{API}/applications/{app_id}/submissions/{pending['id']}", token=token)
    print(f"deleted the pending submission {pending['id']}")


def main() -> None:
    """Parses the command line and runs the command."""
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("\n\n", 1)[1],
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name, help_text in [
        ("check", "read the app and its submissions (changes nothing)"),
        ("show", "print the pending or last published submission (changes nothing)"),
        ("submit", "make a new submission (package, listings) and commit it"),
        ("commit", "commit the pending submission (a draft)"),
        ("discard", "delete the pending submission"),
    ]:
        command = sub.add_parser(name, help=help_text)
        command.add_argument("--app-id", required=True, help="the Store ID, such as 9P6KDQX7JZS4")
        if name == "submit":
            command.add_argument(
                "--package", type=Path, action="append", default=[],
                help="the .msix to replace the packages with (repeat it for each architecture's)",
            )
            command.add_argument("--listings", type=Path, help="the folder of listing files (*.toml)")
            command.add_argument(
                "--images", action="store_true", help="also replace the screenshots"
            )
            command.add_argument(
                "--no-commit", action="store_true", help="leave the submission as a draft"
            )
    args = parser.parse_args()
    if args.command == "check":
        check(args.app_id)
    elif args.command == "show":
        show(args.app_id)
    elif args.command == "submit":
        for package in args.package:
            if not package.is_file():
                raise SystemExit(f"no package {package}")
        names = [package.name for package in args.package]
        if len(set(names)) != len(names):
            raise SystemExit(f"the packages need different file names: {names}")
        submit(args.app_id, args.package, args.listings, args.images, not args.no_commit)
    elif args.command == "commit":
        commit_pending(args.app_id)
    elif args.command == "discard":
        discard(args.app_id)


if __name__ == "__main__":
    main()
