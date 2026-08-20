#!/usr/bin/env bash
# 构建并推送 ComWorker 全部交付镜像到私有镜像仓库。
# 前置：已 `docker login` 到目标仓库。
# 用法：
#   COMWORKER_REGISTRY=registry.cn-xxx.aliyuncs.com/your-namespace COMWORKER_TAG=1.0.0 bash push_to_registry.sh
#
# 镜像清单（推送到 $REGISTRY/<name>:<TAG>）：
#   comworker-gateway, comworker-manage, comworker-cca-front, comworker-hermes-agent, comworker-postgres
set -euo pipefail

REGISTRY="${COMWORKER_REGISTRY:-registry.example.com}"
TAG="${COMWORKER_TAG:-latest}"
PG_TAG="${COMWORKER_PG_TAG:-16-alpine}"
PIP_INDEX_URL="${PIP_INDEX_URL:-https://mirrors.aliyun.com/pypi/simple}"
APT_DEBIAN_MIRROR="${APT_DEBIAN_MIRROR:-http://mirrors.ustc.edu.cn/debian}"
APT_SECURITY_MIRROR="${APT_SECURITY_MIRROR:-http://mirrors.ustc.edu.cn/debian-security}"

echo "==> [1/5] 构建后端与管理端 (compose build gateway manage-front)"
docker compose build gateway manage-front

echo "==> [2/5] 构建客户端生产镜像 (cca-front)"
docker build -f cca-front/Dockerfile -t "comworker-cca-front:${TAG}" cca-front/

echo "==> [3/5] 构建 hermes 运行时镜像"
docker build -f hermes-agent/Dockerfile.bridge -t "comworker-hermes-agent:${TAG}" \
  --build-arg PIP_INDEX_URL="$PIP_INDEX_URL" \
  --build-arg APT_DEBIAN_MIRROR="$APT_DEBIAN_MIRROR" \
  --build-arg APT_SECURITY_MIRROR="$APT_SECURITY_MIRROR" \
  hermes-agent/

echo "==> [4/5] 拉取并标记 postgres"
docker pull "postgres:${PG_TAG}"
docker tag "postgres:${PG_TAG}" "${REGISTRY}/comworker-postgres:${PG_TAG}"

echo "==> [5/5] 打 registry 标签并推送"
for name in comworker-gateway comworker-manage comworker-cca-front comworker-hermes-agent; do
  docker tag "${name}:${TAG}" "${REGISTRY}/${name}:${TAG}"
  docker push "${REGISTRY}/${name}:${TAG}"
done
docker push "${REGISTRY}/comworker-postgres:${PG_TAG}"

echo "==> 完成。客户侧执行："
echo "    export COMWORKER_REGISTRY=${REGISTRY} COMWORKER_TAG=${TAG}"
echo "    docker compose -f docker-compose.yml -f docker-compose.registry.yml pull"
echo "    docker compose -f docker-compose.yml -f docker-compose.registry.yml up -d"
