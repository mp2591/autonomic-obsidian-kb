FROM ubuntu:24.04

ARG OBSIDIAN_VERSION
LABEL org.opencontainers.image.title="autonomic-obsidian-kb real Obsidian CI"
LABEL org.opencontainers.image.version="${OBSIDIAN_VERSION}"

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp/obsidian-home \
    XDG_CONFIG_HOME=/tmp/obsidian-home/.config \
    XDG_CACHE_HOME=/tmp/obsidian-home/.cache \
    XDG_DATA_HOME=/tmp/obsidian-home/.local/share \
    XDG_RUNTIME_DIR=/tmp/obsidian-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates \
      git \
      libasound2t64 \
      libdrm2 \
      libgbm1 \
      libgtk-3-0 \
      libnotify4 \
      libnss3 \
      libsecret-1-0 \
      libxss1 \
      procps \
      python3 \
      python3-jsonschema \
      python3-yaml \
      tini \
      xdg-utils \
    && rm -rf /var/lib/apt/lists/*

COPY Obsidian.AppImage /tmp/Obsidian.AppImage
RUN set -eux; \
    chmod 0755 /tmp/Obsidian.AppImage; \
    cd /opt; \
    /tmp/Obsidian.AppImage --appimage-extract >/tmp/appimage-extract.log; \
    mv /opt/squashfs-root /opt/obsidian; \
    rm /tmp/Obsidian.AppImage; \
    test -x /opt/obsidian/AppRun; \
    test -x /opt/obsidian/obsidian; \
    CLI_PATH="$(find -L /opt/obsidian -type f -name obsidian-cli -print -quit)"; \
    if [ -z "$CLI_PATH" ]; then \
      echo "The extracted official AppImage did not expose obsidian-cli as a regular file." >&2; \
      echo "Obsidian/CLI-related extracted entries:" >&2; \
      find /opt/obsidian -maxdepth 8 \( -iname '*obsidian*' -o -iname '*cli*' \) -printf '%y %p -> %l\n' | sort >&2; \
      exit 1; \
    fi; \
    echo "Installing first-party CLI from $CLI_PATH"; \
    install -m 0755 "$CLI_PATH" /usr/local/bin/obsidian; \
    mkdir -p "$HOME" "$XDG_CONFIG_HOME/obsidian" "$XDG_CACHE_HOME" "$XDG_DATA_HOME" "$XDG_RUNTIME_DIR" /artifacts; \
    chmod 0700 "$XDG_RUNTIME_DIR"

COPY entrypoint.sh /usr/local/bin/obsidian-ci-entrypoint
RUN chmod 0755 /usr/local/bin/obsidian-ci-entrypoint

WORKDIR /workspace
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/obsidian-ci-entrypoint"]
