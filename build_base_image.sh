#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BUILD_DIR="${SCRIPT_DIR}/hermes-agent"
IMAGE_NAME="hermes-base"
IMAGE_TAG="${1:-latest}"
DOCKERFILE="${BUILD_DIR}/Dockerfile"
APT_DEBIAN_MIRROR="${APT_DEBIAN_MIRROR:-http://mirrors.ustc.edu.cn/debian}"
APT_SECURITY_MIRROR="${APT_SECURITY_MIRROR:-http://mirrors.ustc.edu.cn/debian-security}"

echo "正在构建镜像 ${IMAGE_NAME}:${IMAGE_TAG} ..."
echo "Dockerfile 路径: ${DOCKERFILE}"
echo "构建上下文: ${BUILD_DIR}"

docker build \
  --build-arg "APT_DEBIAN_MIRROR=${APT_DEBIAN_MIRROR}" \
  --build-arg "APT_SECURITY_MIRROR=${APT_SECURITY_MIRROR}" \
  -t "${IMAGE_NAME}:${IMAGE_TAG}" \
  -f "${DOCKERFILE}" \
  "${BUILD_DIR}"

echo "构建完成: ${IMAGE_NAME}:${IMAGE_TAG}"
