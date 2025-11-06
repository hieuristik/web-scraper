#!/usr/bin/env bash
# Build and push a multi-arch Docker image using docker buildx (for WSL / Linux / macOS)
# Usage: ./scripts/build-and-push.sh yourusername/aa-scraper:tag
set -euo pipefail

IMAGE="${1:-hieuristik/aa-scraper:latest}"

echo "Using image: $IMAGE"

# create buildx builder if missing
if ! docker buildx inspect multi-builder >/dev/null 2>&1; then
  echo "Creating buildx builder 'multi-builder'..."
  docker buildx create --name multi-builder --use
else
  echo "Using existing buildx builder 'multi-builder'..."
  docker buildx use multi-builder
fi

echo "Bootstrapping buildx..."
docker buildx inspect --bootstrap

echo "Building and pushing multi-arch image for linux/amd64 and linux/arm64..."
docker buildx build --platform linux/amd64,linux/arm64 -t "$IMAGE" --push .

echo "Done. Pushed $IMAGE"