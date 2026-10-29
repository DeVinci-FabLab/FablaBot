#!/usr/bin/env bash
# Writes the tags and version label of one image build to $GITHUB_OUTPUT.
# Every push to main gets :edge and :sha-<commit>; a release adds :latest, :X.Y.Z and :X.Y.
set -euo pipefail

image=ghcr.io/devinci-fablab/fablabot

if [[ ! "${GITHUB_SHA:-}" =~ ^[0-9a-f]{40}$ ]]; then
  echo "Not a commit SHA: '${GITHUB_SHA:-}'" >&2
  exit 1
fi

tags="$image:edge
$image:sha-$GITHUB_SHA"
version=edge

if [[ "${RELEASE_CREATED:-}" == "true" ]]; then
  # A malformed version must never move :latest.
  if [[ ! "${VERSION:-}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "Not a release version: '${VERSION:-}'" >&2
    exit 1
  fi
  tags="$tags
$image:latest
$image:$VERSION
$image:${VERSION%.*}"
  version=$VERSION
fi

{
  echo "tags<<EOF"
  printf '%s\n' "$tags"
  echo "EOF"
  echo "version=$version"
} >> "${GITHUB_OUTPUT:?}"
