#!/usr/bin/env bash
# Download benchmark task definitions into ./data (idempotent).
#   bash data/download.sh
set -euo pipefail

cd "$(dirname "$0")"

fetch_tarball() {
  # $1 dest-dir  $2 repo-url(tar.gz)  $3 extracted-prefix
  local dest="$1" url="$2" prefix="$3"
  if [ -d "$dest" ]; then
    echo "$dest already present"
    return
  fi
  echo "downloading $dest ..."
  curl -fL -o "$dest.tar.gz" "$url"
  tar xzf "$dest.tar.gz"
  mv "$prefix" "$dest"
  rm -f "$dest.tar.gz"
  echo "done: $dest"
}

fetch_tarball \
  terminal-bench-2-1 \
  "https://codeload.github.com/harbor-framework/terminal-bench-2-1/tar.gz/refs/heads/main" \
  terminal-bench-2-1-main

fetch_tarball \
  nl2repo \
  "https://codeload.github.com/multimodal-art-projection/NL2RepoBench/tar.gz/refs/heads/main" \
  NL2RepoBench-main

echo
echo "terminal-bench tasks: $(ls terminal-bench-2-1/tasks 2>/dev/null | wc -l)"
echo "nl2repo tasks       : $(ls -d nl2repo/test_files/*/ 2>/dev/null | wc -l)"
echo "note: task container images are pulled at run time (terminal-bench: docker hub; nl2repo: ghcr.io)"
