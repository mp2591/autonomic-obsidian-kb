#!/usr/bin/env python3
"""Download and integrity-check the pinned official Obsidian Linux AppImage.

GitHub computes an immutable SHA-256 digest for release assets. CI resolves the
exact versioned AppImage through the releases API, verifies that digest, and
only then supplies the binary to the isolated integration-test image.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"
RELEASE_API = "https://api.github.com/repos/obsidianmd/obsidian-releases/releases/tags/v{version}"


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    version: str
    name: str
    url: str
    digest: str
    size: int
    release_url: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def select_appimage_asset(release: dict[str, Any], version: str) -> ReleaseAsset:
    expected = f"Obsidian-{version}.AppImage"
    matches = [asset for asset in release.get("assets", []) if asset.get("name") == expected]
    if len(matches) != 1:
        available = sorted(str(asset.get("name", "")) for asset in release.get("assets", []))
        raise RuntimeError(f"expected exactly one {expected!r} release asset; found {available}")
    raw = matches[0]
    digest = str(raw.get("digest") or "")
    if not digest.startswith("sha256:") or len(digest.removeprefix("sha256:")) != 64:
        raise RuntimeError(f"release asset {expected!r} has no immutable SHA-256 digest")
    url = str(raw.get("browser_download_url") or "")
    if not url.startswith("https://github.com/obsidianmd/obsidian-releases/releases/download/"):
        raise RuntimeError(f"refusing unexpected release asset URL: {url!r}")
    return ReleaseAsset(
        version=version,
        name=expected,
        url=url,
        digest=digest,
        size=int(raw.get("size") or 0),
        release_url=str(release.get("html_url") or ""),
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _request(url: str, token: str = "") -> urllib.request.Request:
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "autonomic-obsidian-kb-ci",
        "X-GitHub-Api-Version": API_VERSION,
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.Request(url, headers=headers)


def fetch_release(version: str, token: str = "") -> dict[str, Any]:
    try:
        with urllib.request.urlopen(_request(RELEASE_API.format(version=version), token), timeout=30) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"GitHub release API returned HTTP {error.code}: {detail}") from error


def download_asset(asset: ReleaseAsset, output: Path) -> None:
    expected = asset.digest.removeprefix("sha256:")
    if output.exists() and sha256_file(output) == expected:
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=output.name, suffix=".part", dir=output.parent, delete=False) as handle:
        temporary = Path(handle.name)
    try:
        request = urllib.request.Request(
            asset.url,
            headers={"User-Agent": "autonomic-obsidian-kb-ci"},
        )
        with urllib.request.urlopen(request, timeout=180) as response, temporary.open("wb") as destination:
            shutil.copyfileobj(response, destination, length=1024 * 1024)
        actual = sha256_file(temporary)
        if actual != expected:
            raise RuntimeError(f"Obsidian AppImage digest mismatch: expected {expected}, got {actual}")
        if asset.size and temporary.stat().st_size != asset.size:
            raise RuntimeError(
                f"Obsidian AppImage size mismatch: expected {asset.size}, got {temporary.stat().st_size}"
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)


def write_metadata(asset: ReleaseAsset, output: Path, metadata_path: Path) -> None:
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        **asset.to_dict(),
        "local_path": str(output),
        "verified_sha256": sha256_file(output),
        "downloaded_at": datetime.now(UTC).isoformat(),
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="Exact public desktop release, for example 1.13.7")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    token = os.environ.get("GITHUB_TOKEN", "")
    release = fetch_release(arguments.version, token)
    asset = select_appimage_asset(release, arguments.version)
    download_asset(asset, arguments.output)
    write_metadata(asset, arguments.output, arguments.metadata)
    print(json.dumps(asset.to_dict(), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
