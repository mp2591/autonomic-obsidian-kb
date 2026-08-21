# Testing strategy

The project has two independent compatibility obligations:

1. The KB must work in ordinary headless agent environments where Obsidian is absent.
2. The same vault and metadata must behave correctly inside the real Obsidian desktop application and its first-party CLI.

Passing only the first obligation is not treated as proof of Obsidian compatibility.

## CI test layers

### 1. Core Python matrix

GitHub Actions runs Python 3.11, 3.12, and 3.13 on `ubuntu-latest` and performs:

- bytecode compilation of `src`, `scripts`, and `tests`;
- the full dependency-free unit suite;
- `kb index` and token-budgeted `kb retrieve` smoke tests;
- the deterministic benchmark guardrail.

These checks prove that filesystem mode, indexing, retrieval, lifecycle logic, and agent interfaces do not require Obsidian.

### 2. Real Obsidian desktop container

A separate job runs the public Linux desktop application, not a mock and not a community CLI.

The job:

1. Resolves the exact `Obsidian-<version>.AppImage` asset from the official `obsidianmd/obsidian-releases` release.
2. Requires and verifies GitHub's immutable `sha256:` release-asset digest.
3. Builds an isolated Ubuntu 24.04 image and extracts the AppImage without FUSE.
4. Registers a fixture vault in `~/.config/obsidian/obsidian.json` and enables the first-party CLI with `"cli": true` before app startup.
5. Unsets `DISPLAY` and `WAYLAND_DISPLAY`; the image intentionally does not install Xvfb.
6. Starts the actual Electron application with `--ozone-platform=headless`, `--no-sandbox`, and software-compatible CI flags.
7. Reads `/proc/<pid>/cmdline` and fails unless the live process contains `--ozone-platform=headless`.
8. Waits until the official `obsidian` CLI can address the configured vault.
9. Re-reads the app-written global configuration and fails unless CLI activation and the registered vault survived startup.
10. Runs the end-to-end contract suite with container networking disabled.
11. Requires the real headless renderer to produce a valid PNG through `dev:screenshot`.
12. Uploads the release metadata, app logs, CLI transcript, process information, metadata snapshot, developer errors, screenshot, and integration report.

The desktop version is pinned in `.github/workflows/ci.yml`. A version update is therefore an explicit compatibility event rather than an unreviewed moving dependency.

## Real-application contract

`scripts/test_obsidian_ci.py` verifies the following against the running application:

- installed version and required command discovery;
- vault registration and app readiness;
- file and folder indexing;
- exact-path reads;
- scalar and list frontmatter properties;
- frontmatter and inline tags;
- outgoing links, backlinks, nested links, and unresolved links;
- full-text search, tasks, and outlines;
- direct metadata-cache access through the official `eval` developer command;
- parity between Obsidian's metadata cache and the KB parser/index;
- token-budgeted KB retrieval from the same live vault;
- Obsidian CLI create, read, property mutation, append, and prepend operations;
- Obsidian's link-aware rename behavior;
- Obsidian noticing a note created directly by the KB/filesystem;
- the KB noticing and indexing notes and property changes created through Obsidian;
- the production `ObsidianBridge` targeting the live registered vault;
- a valid PNG rendered by the actual Ozone-headless application;
- survival of the app process through the complete test sequence.

The fixture intentionally includes nested paths, aliases, booleans, numbers, frontmatter tags, inline tags, tasks, headings, resolved links, backlinks, and an unresolved link.

## Failure artifacts

The `real-obsidian-*` workflow artifact is uploaded even when the job fails. Important files include:

- `obsidian-release.json`: official asset URL, size, and verified digest;
- `obsidian-app.log`: Electron/Obsidian process output;
- `obsidian-process-command.txt`: the live app command line;
- `obsidian-config.json` and `obsidian-config-after-start.json`: CLI/vault configuration before and after app startup;
- `obsidian-cli-transcript.jsonl`: every command, exit code, duration, stdout, and stderr;
- `obsidian-metadata-snapshot.json`: metadata obtained from the running app;
- `obsidian-integration-report.json`: assertion-level results;
- `obsidian-workspace.png`: screenshot emitted by the real headless renderer;
- `obsidian-dev-errors.txt` and `obsidian-console-errors.txt`: app-level diagnostics.

## Local reproduction

Docker and network access to GitHub releases are required:

```bash
export OBSIDIAN_VERSION=1.13.7
python scripts/download_obsidian_ci.py \
  --version "$OBSIDIAN_VERSION" \
  --output .ci-cache/Obsidian.AppImage \
  --metadata .ci-cache/obsidian-release.json

cp .ci-cache/Obsidian.AppImage ci/obsidian/Obsidian.AppImage

docker build \
  --build-arg OBSIDIAN_VERSION="$OBSIDIAN_VERSION" \
  -t autonomic-obsidian-kb-obsidian-ci \
  ci/obsidian

mkdir -p test-artifacts
docker run --rm --network=none --shm-size=1g \
  -e OBSIDIAN_VERSION="$OBSIDIAN_VERSION" \
  -v "$PWD:/workspace:ro" \
  -v "$PWD/test-artifacts:/artifacts:rw" \
  autonomic-obsidian-kb-obsidian-ci
```

## Remaining compatibility matrix

The real-app gate currently covers the official Linux x86-64 AppImage and the pinned public desktop release. macOS, Windows, ARM64 Linux, community plugins, Sync, and large-vault performance require separate runners or fixtures. Those are not implied by a green Linux job.
