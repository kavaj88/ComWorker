#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -p comworker-offline -f compose.yml)

die() { printf '安装失败: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }

command -v docker >/dev/null 2>&1 || die "未检测到 Docker"
docker info >/dev/null 2>&1 || die "Docker daemon 未运行"
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2"
case "$(uname -m)" in x86_64|amd64) ;; *) die "当前离线包仅支持 amd64/x86_64" ;; esac

if command -v sha256sum >/dev/null 2>&1; then
  info "校验离线包"
  sha256sum -c checksums.sha256 >/dev/null || die "文件校验失败"
fi

version="$(sed -n 's/^COMWORKER_VERSION=//p' .env.example | head -n 1)"
images=(
  "comworker-gateway:$version"
  "comworker-manage:$version"
  "comworker-cca-front:$version"
  "comworker-hermes-agent:$version"
  "comworker-postgres:16-alpine"
)

need_load=0
for image in "${images[@]}"; do
  docker image inspect "$image" >/dev/null 2>&1 || need_load=1
done
if [ "$need_load" -eq 1 ]; then
  info "导入预置镜像（首次安装耗时取决于磁盘速度）"
  docker load -i images/comworker-images.tar.zst
else
  info "镜像已存在，跳过导入"
fi

umask 077
if [ ! -f .env ]; then
  cp .env.example .env
fi
mkdir -p secrets

generate_secret() {
  docker run --rm --entrypoint sh comworker-postgres:16-alpine -c \
    'head -c 48 /dev/urandom | base64 | tr -d "\n"'
}

ensure_secret() {
  local path="$1"
  if [ ! -s "$path" ]; then
    generate_secret > "$path"
  fi
}

ensure_secret secrets/jwt_secret
ensure_secret secrets/model_keys_master_key
ensure_secret secrets/admin_password

if ! grep -q '^POSTGRES_PASSWORD=.' .env; then
  db_password="$(generate_secret)"
  awk -v password="$db_password" '
    BEGIN { done=0 }
    /^POSTGRES_PASSWORD=/ { print "POSTGRES_PASSWORD=" password; done=1; next }
    { print }
    END { if (!done) print "POSTGRES_PASSWORD=" password }
  ' .env > .env.new
  mv .env.new .env
fi

info "启动 ComWorker"
"${COMPOSE[@]}" up -d

wait_healthy() {
  local service="$1" deadline=$((SECONDS + 180)) id status
  while [ "$SECONDS" -lt "$deadline" ]; do
    id="$("${COMPOSE[@]}" ps -q "$service")"
    if [ -n "$id" ]; then
      status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id")"
      [ "$status" = healthy ] && return 0
      [ "$status" = exited ] && return 1
    fi
    sleep 2
  done
  return 1
}

for service in postgres gateway manage client; do
  info "等待 $service 就绪"
  wait_healthy "$service" || {
    "${COMPOSE[@]}" ps
    die "$service 未能通过健康检查，请运行 ./comworker.sh logs"
  }
done

client_port="$(sed -n 's/^COMWORKER_CLIENT_PORT=//p' .env | head -n 1)"
manage_port="$(sed -n 's/^COMWORKER_MANAGE_PORT=//p' .env | head -n 1)"
admin_user="$(sed -n 's/^ADMIN_USERNAME=//p' .env | head -n 1)"

printf '\n安装完成\n'
printf '客户端: http://localhost:%s\n' "${client_port:-3000}"
printf '管理端: http://localhost:%s\n' "${manage_port:-3081}"
printf '管理员: %s\n' "${admin_user:-admin}"
printf '初始密码: %s\n' "$(cat secrets/admin_password)"
printf '运维命令: ./comworker.sh status|logs|backup|uninstall\n'
