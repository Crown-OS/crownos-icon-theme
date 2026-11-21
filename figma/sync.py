#!/usr/bin/env python3
"""Exports the CrownOS icon pack from Figma into this repository.

    figma/sync.py [--force] [--export NAME ...] [--pending-version ID] [--report FILE]

The sync reads the newest named version of the Figma file. It writes an SVG
only for an icon that is new, or that changed since the last sync. Icons that
contain raster images are not written, because the theme ships vector SVGs only.

The FIGMA_TOKEN environment variable must hold a Figma personal access token
with the file_content:read and file_versions:read scopes.
"""

import argparse
import concurrent.futures
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = REPOSITORY_ROOT / "figma" / "config.json"
MANIFEST_PATH = REPOSITORY_ROOT / "figma" / "manifest.json"
FIGMA_API = "https://api.figma.com/v1"
EXPORT_BATCH_SIZE = 200
DOWNLOAD_WORKERS = 8
MAX_ATTEMPTS = 6
VERSION_PAGES_TO_SEARCH = 10
POSITION_KEYS = ("absoluteBoundingBox", "absoluteRenderBounds")


@dataclass
class Icon:
    path: str
    node_id: str
    fingerprint: str
    status: str


def figma_get(path, params=None):
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    request = urllib.request.Request(
        f"{FIGMA_API}{path}{query}", headers={"X-Figma-Token": os.environ["FIGMA_TOKEN"]}
    )
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=300) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            retryable = error.code == 429 or error.code >= 500
            if not retryable or attempt == MAX_ATTEMPTS:
                raise SystemExit(f"Figma {path} failed: HTTP {error.code} {error.read()[:300]!r}")
            wait_seconds = int(error.headers.get("Retry-After") or 2**attempt)
            print(f"Figma {path}: HTTP {error.code}, retry in {wait_seconds}s", file=sys.stderr)
            time.sleep(wait_seconds)


def download(url):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(url, timeout=120) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == MAX_ATTEMPTS:
                raise
            time.sleep(2**attempt)


def latest_named_version(file_key):
    params = {}
    for _ in range(VERSION_PAGES_TO_SEARCH):
        page = figma_get(f"/files/{file_key}/versions", params)
        named = [version for version in page["versions"] if version.get("label")]
        if named:
            newest = max(named, key=lambda version: version["created_at"])
            return {key: newest[key] for key in ("id", "label", "created_at")}
        next_page = page.get("pagination", {}).get("next_page")
        if not next_page:
            break
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(next_page).query))
    return None


def icon_frame(grid_cell):
    frames = [child for child in grid_cell.get("children", []) if child["type"] in ("FRAME", "COMPONENT", "INSTANCE")]
    return frames[0] if frames else grid_cell


def relative_to(node, origin):
    canonical = {}
    for key, value in node.items():
        if key == "id":
            continue
        if key in POSITION_KEYS and value:
            value = {**value, "x": round(value["x"] - origin["x"], 3), "y": round(value["y"] - origin["y"], 3)}
        elif key == "children":
            value = [relative_to(child, origin) for child in value]
        canonical[key] = value
    return canonical


def fingerprint(frame):
    canonical = relative_to(frame, frame["absoluteBoundingBox"])
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def contains_raster(node):
    paints = node.get("fills", []) + node.get("strokes", [])
    if any(paint.get("type") == "IMAGE" and paint.get("visible", True) for paint in paints):
        return True
    return any(contains_raster(child) for child in node.get("children", []))


def is_safe_file_name(name):
    return bool(name) and "/" not in name and not name.startswith(".")


def plan_icons(config, documents, manifest, forced_names):
    seen_paths = {}
    icons = []
    incomplete = manifest.get("incomplete")
    retry_paths = set(incomplete["failed"]) if incomplete else set()
    initial_import = manifest["version"] is None and incomplete is None
    for grid in config["grids"]:
        for grid_cell in documents[grid["node"]]["children"]:
            name = grid_cell["name"]
            path = f"{grid['dest']}/{name}.svg"
            if path in seen_paths:
                seen_paths[path].status = "duplicate"
                continue
            frame = icon_frame(grid_cell)
            icon = Icon(path, frame["id"], fingerprint(frame), "")
            seen_paths[path] = icon
            icons.append(icon)
            if not is_safe_file_name(name):
                icon.status = "invalid-name"
            elif contains_raster(frame) or manifest["rejected"].get(path) == icon.fingerprint:
                icon.status = "raster"
            elif name in forced_names:
                icon.status = "forced"
            elif path not in retry_paths and manifest["icons"].get(path) == icon.fingerprint:
                icon.status = "unchanged"
            elif path in manifest["icons"] or path in manifest["rejected"]:
                icon.status = "changed"
            elif initial_import and (
                (REPOSITORY_ROOT / path).exists()
                or ("fallback" in grid and (REPOSITORY_ROOT / grid["fallback"] / f"{name}.svg").exists())
            ):
                icon.status = "baseline"
            else:
                icon.status = "new"
    return icons


def export_svgs(file_key, version_id, icons):
    image_urls = {}
    for start in range(0, len(icons), EXPORT_BATCH_SIZE):
        batch = icons[start : start + EXPORT_BATCH_SIZE]
        response = figma_get(
            f"/images/{file_key}",
            {
                "ids": ",".join(icon.node_id for icon in batch),
                "format": "svg",
                "svg_outline_text": "true",
                "svg_simplify_stroke": "true",
                "version": version_id,
            },
        )
        image_urls.update(response["images"])

    def write_icon(icon):
        url = image_urls.get(icon.node_id)
        if not url:
            return icon, "export-failed"
        try:
            svg = download(url)
        except (urllib.error.URLError, TimeoutError) as error:
            print(f"Could not download SVG for node {icon.node_id}: {type(error).__name__}", file=sys.stderr)
            return icon, "export-failed"
        if b"<svg" not in svg[:512]:
            return icon, "export-failed"
        if b"<image" in svg:
            return icon, "raster"
        destination = REPOSITORY_ROOT / icon.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(svg)
        return icon, icon.status

    with concurrent.futures.ThreadPoolExecutor(DOWNLOAD_WORKERS) as pool:
        for icon, status in pool.map(write_icon, icons):
            icon.status = status


def updated_manifest(version, icons, previous_manifest):
    tracked = dict(previous_manifest["icons"])
    rejected = dict(previous_manifest["rejected"])
    for icon in icons:
        if icon.status in ("new", "changed", "forced", "baseline", "unchanged"):
            tracked[icon.path] = icon.fingerprint
            rejected.pop(icon.path, None)
        elif icon.status == "raster":
            rejected[icon.path] = icon.fingerprint
    failed = sorted(icon.path for icon in icons if icon.status == "export-failed")
    result = {
        "version": previous_manifest["version"] if failed else version,
        "icons": dict(sorted(tracked.items())),
        "rejected": dict(sorted(rejected.items())),
    }
    if failed:
        result["incomplete"] = {"version": version, "failed": failed}
    return result


def report(version, icons, missing_paths):
    sections = [
        ("Added", "new"),
        ("Updated", "changed"),
        ("Re-exported on request", "forced"),
        ("Not written: the icon contains a raster image", "raster"),
        ("Not written: the name is used by more than one icon", "duplicate"),
        ("Not written: the name is not a safe file name", "invalid-name"),
        ("Not written: the Figma export failed", "export-failed"),
    ]
    lines = [f"Figma version **{version['label']}** ({version['created_at']}, id `{version['id']}`).", ""]
    for title, status in sections:
        paths = sorted(icon.path for icon in icons if icon.status == status)
        if paths:
            lines += [f"<details><summary>{title}: {len(paths)}</summary>", "", *[f"- `{path}`" for path in paths], "", "</details>", ""]
    if missing_paths:
        lines += [f"<details><summary>In the repository but no longer in Figma: {len(missing_paths)}</summary>", ""]
        lines += [f"- `{path}`" for path in missing_paths] + ["", "</details>", ""]
    return "\n".join(lines)


def set_github_output(**values):
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a") as output:
            output.writelines(f"{key}={value}\n" for key, value in values.items())


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--force", action="store_true", help="sync even if the named version was synced before")
    parser.add_argument("--export", nargs="*", default=[], metavar="NAME", help="export these icons even if unchanged")
    parser.add_argument("--pending-version", help="version id of a sync that waits for review")
    parser.add_argument("--report", type=Path, help="write a Markdown summary to this file")
    arguments = parser.parse_args()

    config = json.loads(CONFIG_PATH.read_text())
    manifest = json.loads(MANIFEST_PATH.read_text()) if MANIFEST_PATH.exists() else {"version": None, "icons": {}, "rejected": {}}
    version = latest_named_version(config["file_key"])
    if version is None:
        print("The Figma file has no named version yet. Save one with File > Save to version history.")
        set_github_output(changed="false")
        return
    synced_versions = {(manifest["version"] or {}).get("id"), arguments.pending_version}
    if version["id"] in synced_versions and not (manifest.get("incomplete") or arguments.force or arguments.export):
        print(f"Named version {version['label']!r} is already synced.")
        set_github_output(changed="false")
        return

    documents = {}
    for grid_id in sorted({grid["node"] for grid in config["grids"]}):
        response = figma_get(
            f"/files/{config['file_key']}/nodes", {"ids": grid_id, "version": version["id"], "geometry": "paths"}
        )
        documents[grid_id] = response["nodes"][grid_id]["document"]

    icons = plan_icons(config, documents, manifest, set(arguments.export))
    to_export = [icon for icon in icons if icon.status in ("new", "changed", "forced")]
    print(f"{len(icons)} icons, {len(to_export)} to export.")
    export_svgs(config["file_key"], version["id"], to_export)

    figma_paths = {icon.path for icon in icons}
    missing_paths = sorted(set(manifest["icons"]) - figma_paths)
    MANIFEST_PATH.write_text(json.dumps(updated_manifest(version, icons, manifest), indent=1) + "\n")
    summary = report(version, icons, missing_paths)
    if arguments.report:
        arguments.report.write_text(summary)
    print(summary)
    set_github_output(changed="true", version=version["id"], label=version["label"])


if __name__ == "__main__":
    main()
