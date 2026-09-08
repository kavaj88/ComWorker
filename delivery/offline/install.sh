#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -p comworker-offline -f compose.yml)
STATE_FILE="$ROOT/.comworker-install.json"
DATABASE_VOLUME="comworker-offline-postgres-data"
UPGRADE_FROM=""
ADOPT_EXISTING=0

die() { printf '安装失败: %s\n' "$*" >&2; exit 1; }
info() { printf '==> %s\n' "$*"; }

usage() {
  cat <<'EOF'
用法:
  ./install.sh
  ./install.sh --upgrade-from /旧版本安装目录
  ./install.sh --adopt-existing
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --upgrade-from)
      [ "$#" -ge 2 ] || die "--upgrade-from 后必须填写旧版本安装目录"
      UPGRADE_FROM="$2"
      shift 2
      ;;
    --adopt-existing)
      ADOPT_EXISTING=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      die "未知参数: $1"
      ;;
  esac
done

command -v docker >/dev/null 2>&1 || die "未检测到 Docker"
command -v sha256sum >/dev/null 2>&1 || die "未检测到 sha256sum，无法校验安装包"
docker info >/dev/null 2>&1 || die "Docker 服务未运行"
docker compose version >/dev/null 2>&1 || die "需要 Docker Compose v2"
[ "$(docker info --format '{{.OSType}}')" = "linux" ] || die "Docker 必须运行 Linux 容器"
case "$(docker info --format '{{.Architecture}}')" in x86_64|amd64) ;; *) die "当前离线包仅支持 amd64/x86_64" ;; esac
[ -f manifest.json ] || die "缺少 manifest.json"
[ -f checksums.sha256 ] || die "缺少 checksums.sha256"
[ -f images/comworker-images.tar.zst ] || die "缺少镜像归档"

info "校验安装包"
sha256sum -c checksums.sha256 >/dev/null || die "文件缺失或校验值不一致，请重新获取安装包"

version="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' manifest.json | head -n 1)"
[ -n "$version" ] || die "manifest.json 中缺少版本号"
images=(
  "comworker-gateway:$version"
  "comworker-manage:$version"
  "comworker-cca-front:$version"
  "comworker-hermes-agent:$version"
  "comworker-postgres:16-alpine"
)

manifest_image_id() {
  local wanted="$1"
  awk -v wanted="$wanted" '
    /"ref":[[:space:]]*"/ {
      line=$0
      sub(/^.*"ref":[[:space:]]*"/, "", line)
      sub(/".*$/, "", line)
      ref=line
    }
    /"id":[[:space:]]*"/ {
      line=$0
      sub(/^.*"id":[[:space:]]*"/, "", line)
      sub(/".*$/, "", line)
      if (ref == wanted) { print line; exit }
    }
  ' manifest.json
}

need_load=0
for image in "${images[@]}"; do
  expected_id="$(manifest_image_id "$image")"
  [ -n "$expected_id" ] || die "manifest.json 缺少镜像记录: $image"
  local_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
  [ "$local_id" = "$expected_id" ] || need_load=1
done
if [ "$need_load" -eq 1 ]; then
  info "导入预置镜像（耗时取决于磁盘速度）"
  docker load -i images/comworker-images.tar.zst >/dev/null || die "Docker 镜像导入失败"
else
  info "本地镜像与安装包一致，跳过导入"
fi
for image in "${images[@]}"; do
  expected_id="$(manifest_image_id "$image")"
  local_id="$(docker image inspect --format '{{.Id}}' "$image" 2>/dev/null || true)"
  [ "$local_id" = "$expected_id" ] || die "镜像导入后 ID 仍不一致: $image"
done

archive_bytes="$(wc -c < images/comworker-images.tar.zst | tr -d ' ')"
free_kb="$(df -Pk "$ROOT" | awk 'NR == 2 {print $4}')"
required_kb=$((archive_bytes * 2 / 1024))
[ "${free_kb:-0}" -ge "$required_kb" ] || die "磁盘空间不足，至少需要镜像归档大小的两倍可用空间"

valid_state=0
if [ -f "$STATE_FILE" ] && grep -Eq '"product"[[:space:]]*:[[:space:]]*"ComWorker"' "$STATE_FILE"; then
  valid_state=1
fi

if [ -n "$UPGRADE_FROM" ]; then
  OLD_ROOT="$(cd "$UPGRADE_FROM" 2>/dev/null && pwd)" || die "旧版本安装目录不存在: $UPGRADE_FROM"
  [ -f "$OLD_ROOT/.env" ] || die "旧版本安装目录缺少 .env"
  [ -d "$OLD_ROOT/secrets" ] || die "旧版本安装目录缺少 secrets"
  if [ "$OLD_ROOT" != "$ROOT" ]; then
    cp "$OLD_ROOT/.env" "$ROOT/.env"
    mkdir -p "$ROOT/secrets"
    cp -a "$OLD_ROOT/secrets/." "$ROOT/secrets/"
    if [ -f "$OLD_ROOT/.comworker-install.json" ]; then
      cp "$OLD_ROOT/.comworker-install.json" "$STATE_FILE"
    fi
  fi
  valid_state=1
elif [ "$valid_state" -eq 0 ] && docker volume inspect "$DATABASE_VOLUME" >/dev/null 2>&1; then
  if [ "$ADOPT_EXISTING" -ne 1 ]; then
    die "检测到已有数据卷但没有安装记录。升级请使用 --upgrade-from 旧目录；确认是本机原安装后可使用 --adopt-existing 接管。"
  fi
  [ -f .env ] && [ -d secrets ] || die "接管已有数据卷需要当前目录保留 .env 和 secrets"
  valid_state=1
fi

umask 077
if [ ! -f .env ]; then
  cp .env.example .env
fi
# 兼容旧包：客户配置不保存软件版本，镜像版本只由当前包的 compose.yml 决定。
env_tmp="$(mktemp "$ROOT/.env.XXXXXX")"
grep -v '^COMWORKER_VERSION=' .env > "$env_tmp" || true
mv "$env_tmp" .env
mkdir -p secrets

generate_secret() {
  docker run --rm --entrypoint sh comworker-postgres:16-alpine -c \
    'head -c 48 /dev/urandom | base64 | tr "+/" "-_" | tr -d "\n="'
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
ensure_secret secrets/hermes_api_key
export ADMIN_PASSWORD="$(cat secrets/admin_password)"

if ! grep -q '^POSTGRES_PASSWORD=.' .env; then
  db_password="$(generate_secret)"
  env_tmp="$(mktemp "$ROOT/.env.XXXXXX")"
  awk -v password="$db_password" '
    BEGIN { done=0 }
    /^POSTGRES_PASSWORD=/ { print "POSTGRES_PASSWORD=" password; done=1; next }
    { print }
    END { if (!done) print "POSTGRES_PASSWORD=" password }
  ' .env > "$env_tmp"
  mv "$env_tmp" .env
fi

env_value() { sed -n "s/^$1=//p" .env | head -n 1; }
port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1 && ss -lnt 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)$port$"; then
    return 0
  fi
  docker ps --format '{{.Ports}}' | grep -Eq "(^|[,:])${port}->|:${port}->"
}

if [ "$valid_state" -eq 0 ] && [ -z "$UPGRADE_FROM" ]; then
  client_port="$(env_value COMWORKER_CLIENT_PORT)"
  manage_port="$(env_value COMWORKER_MANAGE_PORT)"
  port_in_use "${client_port:-3000}" && die "端口 ${client_port:-3000} 已被占用，请修改 .env"
  port_in_use "${manage_port:-3081}" && die "端口 ${manage_port:-3081} 已被占用，请修改 .env"
fi

if [ -n "$UPGRADE_FROM" ] && [ "${COMWORKER_BACKUP_DONE:-0}" != 1 ]; then
  info "备份升级前数据"
  BACKUP_SOURCE_DIR="$OLD_ROOT" bash "$ROOT/comworker.sh" backup
fi

info "启动 ComWorker $version"
"${COMPOSE[@]}" config >/dev/null || die "Compose 配置无效"
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

installed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
json_root="$(printf '%s' "$ROOT" | sed 's/\\/\\\\/g; s/"/\\"/g')"
state_tmp="$(mktemp "$ROOT/.comworker-install.XXXXXX")"
cat > "$state_tmp" <<EOF
{
  "product": "ComWorker",
  "version": "$version",
  "install_dir": "$json_root",
  "installed_at": "$installed_at",
  "last_backup": null
}
EOF
mv "$state_tmp" "$STATE_FILE"

client_port="$(env_value COMWORKER_CLIENT_PORT)"
manage_port="$(env_value COMWORKER_MANAGE_PORT)"
admin_user="$(env_value ADMIN_USERNAME)"
printf '\n安装完成\n'
printf '客户端: http://localhost:%s\n' "${client_port:-3000}"
printf '管理端: http://localhost:%s\n' "${manage_port:-3081}"
printf '管理员: %s\n' "${admin_user:-admin}"
printf '初始密码: %s\n' "$(cat secrets/admin_password)"
printf '运维命令: ./comworker.sh status|logs|backup|restore|rollback|uninstall\n'
