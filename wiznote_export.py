#!/usr/bin/env python3
"""Export locally indexed WizNote notes to Markdown and assets."""

import argparse
import getpass
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


LOGIN_SERVER = "https://as.wiz.cn"


class SafeRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urlsplit(newurl).scheme != "https":
            raise ValueError("Refusing a non-HTTPS redirect")
        redirected = super().redirect_request(request, fp, code, msg, headers, newurl)
        if redirected and urlsplit(newurl).netloc != urlsplit(request.full_url).netloc:
            redirected.remove_header("X-Wiz-Token")
        return redirected


OPENER = build_opener(SafeRedirect())


def safe_name(value):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", str(value)).strip(" .")[:100]
    return name if name and name not in (".", "..") else "untitled"


def find_database(username):
    roots = (
        (Path.home() / "Library/Containers").glob("*/Data/Documents/*/index.db"),
        (Path.home() / "Library/Group Containers/group.wiznote.share/Library/Caches/widget").glob("*/index.db"),
    )
    for source, root in enumerate(roots):
        candidates = []
        for db in root:
            try:
                with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
                    if conn.execute("SELECT name FROM sqlite_master WHERE name='WIZ_DOCUMENT'").fetchone():
                        candidates.append(db)
            except sqlite3.DatabaseError:
                pass
        if candidates:
            if source == 0:
                matches = [db for db in candidates if db.parent.name == username]
                if len(matches) == 1:
                    return matches[0]
                raise RuntimeError(f"No unique local index for {username}; pass --db explicitly")
            if len(candidates) == 1:
                return candidates[0]
            raise RuntimeError("Multiple WizNote indexes found; pass --db explicitly")
    raise RuntimeError("No WizNote index.db found; pass --db explicitly")


def indexed_notes(db):
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        return conn.execute(
            "SELECT DOCUMENT_GUID, DOCUMENT_TITLE, DOCUMENT_LOCATION "
            "FROM WIZ_DOCUMENT ORDER BY DOCUMENT_GUID"
        ).fetchall()


def send(url, token=None, data=None):
    if urlsplit(url).scheme != "https":
        raise ValueError("Refusing a non-HTTPS request")
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Wiz-Token"] = token
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = Request(url, data=body, headers=headers)
    try:
        with OPENER.open(request, timeout=30) as response:
            return response.read(), response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code}") from None
    except URLError as exc:
        raise RuntimeError(f"Network error: {exc.reason}") from None


def api_json(url, token=None, data=None):
    raw, _ = send(url, token, data)
    result = json.loads(raw)
    if result.get("returnCode") != 200:
        raise RuntimeError(f"WizNote API error {result.get('returnCode')}: {result.get('returnMessage', '')}")
    return result


def login(username):
    password = getpass.getpass("WizNote password: ")
    try:
        result = api_json(f"{LOGIN_SERVER}/as/user/login", data={"userId": username, "password": password})["result"]
    finally:
        password = None
    server = result["kbServer"].rstrip("/")
    host = urlsplit(server).hostname or ""
    if urlsplit(server).scheme != "https" or not (host == "wiz.cn" or host.endswith(".wiz.cn")):
        raise RuntimeError("Unexpected knowledge-base server in login response")
    return server, result["kbGuid"], result["token"]


def asset_bytes(url, token=None):
    raw, content_type = send(url, token)
    if "json" in content_type.lower():
        raise RuntimeError("Asset endpoint returned JSON instead of a file")
    return raw


def note_markdown(html, note_type, resources, asset_dir, server, token):
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    if note_type == "collaboration":
        raise RuntimeError("Collaboration note needs a separate WebSocket exporter")
    if not isinstance(html, str) or not html.strip():
        raise RuntimeError("Note body is empty")

    assets = {}
    for resource in resources:
        name, url = resource.get("name"), resource.get("url")
        if not name or not url:
            raise RuntimeError("Image resource has no name or URL")
        filename = safe_name(name)
        asset_dir.mkdir(parents=True, exist_ok=True)
        resource_token = token if urlsplit(url).hostname == urlsplit(server).hostname else None
        (asset_dir / filename).write_bytes(asset_bytes(url, resource_token))
        assets[f"index_files/{name}"] = f"{asset_dir.name}/{quote(filename)}"
        assets[url] = f"{asset_dir.name}/{quote(filename)}"

    if note_type == "lite/markdown":
        soup = BeautifulSoup(html, "html.parser")
        content = soup.body.get_text() if soup.body else html
        for source, local in assets.items():
            content = content.replace(source, local)
        return content

    soup = BeautifulSoup(html, "html.parser")
    for unwanted in soup(["script", "style"]):
        unwanted.decompose()
    for element in soup.find_all(True):
        for attribute in ("src", "href"):
            original = element.get(attribute)
            if original in assets:
                element[attribute] = assets[original]
    return markdownify(str(soup.body or soup), heading_style="ATX").strip()


def export_note(note, output, server, kb_guid, token):
    guid, title, location = note
    folder = output.joinpath(*(safe_name(part) for part in (location or "").split("/") if part))
    stem = f"{safe_name(title or 'untitled')}__{guid[:8]}"
    markdown_path = folder / f"{stem}.md"
    if markdown_path.exists():
        return False

    endpoint = f"{server}/ks/note/download/{quote(kb_guid)}/{quote(guid)}?downloadInfo=1&downloadData=1"
    payload = api_json(endpoint, token)
    info = payload.get("info") or {}
    asset_dir = folder / f"{stem}_assets"
    content = note_markdown(payload.get("html"), info.get("type", "document"),
                            payload.get("resources") or [], asset_dir, server, token)

    attachments = api_json(
        f"{server}/ks/note/attachments/{quote(kb_guid)}/{quote(guid)}", token
    ).get("result") or []
    links = []
    for attachment in attachments:
        attachment_guid, name = attachment.get("attGuid"), attachment.get("name")
        if not attachment_guid or not name:
            raise RuntimeError("Attachment has no GUID or name")
        filename = f"{attachment_guid[:8]}_{safe_name(name)}"
        url = f"{server}/ks/attachment/download/{quote(kb_guid)}/{quote(guid)}/{quote(attachment_guid)}"
        asset_dir.mkdir(parents=True, exist_ok=True)
        (asset_dir / filename).write_bytes(asset_bytes(url, token))
        escaped_name = name.replace("]", r"\]")
        links.append(f"- [{escaped_name}]({asset_dir.name}/{quote(filename)})")

    if links:
        content += "\n\n## Attachments\n\n" + "\n".join(links)
    folder.mkdir(parents=True, exist_ok=True)
    temporary = markdown_path.with_suffix(".md.part")
    temporary.write_text(f"<!-- WizNote GUID: {guid} -->\n\n{content.strip()}\n", encoding="utf-8")
    os.replace(temporary, markdown_path)
    return True


def main():
    parser = argparse.ArgumentParser(description="Export locally indexed WizNote notes from the cloud")
    parser.add_argument("--db", type=Path, help="WizNote account index.db; auto-detected if omitted")
    parser.add_argument("--username", required=True, help="WizNote login account")
    parser.add_argument("--output", required=True, type=Path, help="Export directory (for example, a folder in an Obsidian vault)")
    args = parser.parse_args()
    db = args.db or find_database(args.username)
    notes = indexed_notes(db)
    if not notes:
        raise RuntimeError("No notes found in the local index")
    print(f"Indexed notes: {len(notes)}")
    print(f"Login account: {args.username}")
    server, kb_guid, token = login(args.username)
    failures = []
    skipped = 0
    for index, note in enumerate(notes, 1):
        try:
            skipped += not export_note(note, args.output, server, kb_guid, token)
        except (RuntimeError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
            failures.append({"guid": note[0], "error": str(exc)})
        print(f"\rProcessed {index}/{len(notes)}; failed {len(failures)}", end="", flush=True)
    print()
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "export-report.json").write_text(json.dumps({
        "indexed": len(notes), "completed": len(notes) - len(failures),
        "skipped_existing": skipped, "failures": failures
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported or already present: {len(notes) - len(failures)}/{len(notes)}; report: {args.output / 'export-report.json'}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (RuntimeError, ValueError, KeyError, OSError, json.JSONDecodeError) as exc:
        print(f"Export stopped: {exc}", file=sys.stderr)
        sys.exit(1)
