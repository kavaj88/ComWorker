#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -p comworker-offline -f "$ROOT/compose.yml")
HELPER_IMAGE="comworker-postgres:16-alpine"
action="${1:-status}"

die() { printf '操作失败: %s\n' "$*" >&2; exit 1; }
env_value_from() { sed -n "s/^$2=//p" "$1/.env" | head -n 1; }

load_admin_password() {
  local source_root="$1"
  if [ -s "$source_root/secrets/admin_password" ]; then
    export ADMIN_PASSWORD="$(cat "$source_root/secrets/admin_password")"
  fi
}
load_admin_password "$ROOT"

source_version() {
  local source_root="$1" value=""
  if [ -f "$source_root/.comworker-install.json" ]; then
    value="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$source_root/.comworker-install.json" | head -n 1)"
  fi
  if [ -z "$value" ] && [ -f "$source_root/manifest.json" ]; then
    value="$(sed -n 's/^[[:space:]]*"version":[[:space:]]*"\([^"]*\)".*/\1/p' "$source_root/manifest.json" | head -n 1)"
  fi
  if [ -z "$value" ] && [ -f "$source_root/.env" ]; then
    value="$(env_value_from "$source_root" COMWORKER_VERSION)"
  fi
  printf '%s' "${value:-unknown}"
}

archive_volume() {
  local volume="$1" destination="$2"
  docker run --rm \
    -e ARCHIVE_NAME="$volume.tar.zst" \
    -v "$volume:/source:ro" \
    -v "$destination:/backup" \
    --entrypoint sh "$HELPER_IMAGE" -c \
    'set -eu; tar -C /source -cf - . | zstd -T0 -q -o "/backup/$ARCHIVE_NAME"'
}

backup_all() {
  local source_root="${BACKUP_SOURCE_DIR:-$ROOT}"
  [ -f "$source_root/.env" ] || die "安装目录缺少 .env: $source_root"
  [ -d "$source_root/secrets" ] || die "安装目录缺少 secrets: $source_root"
  load_admin_password "$source_root"
  local source_compose=(docker compose -p comworker-offline --env-file "$source_root/.env" -f "$source_root/compose.yml")
  local postgres_id
  postgres_id="$("${source_compose[@]}" ps -q postgres)"
  [ -n "$postgres_id" ] || die "数据库未运行，请先启动 ComWorker 再备份"

  local stamp backup_dir volume_dir config_dir user database version created_at source_json
  stamp="$(date +%Y%m%d-%H%M%S)"
  backup_dir="$ROOT/backups/backup-$stamp"
  volume_dir="$backup_dir/volumes"
  config_dir="$backup_dir/config"
  mkdir -p "$volume_dir" "$config_dir/secrets"

  user="$(env_value_from "$source_root" POSTGRES_USER)"
  database="$(env_value_from "$source_root" POSTGRES_DB)"
  "${source_compose[@]}" exec -T postgres pg_dump -Fc \
    -U "${user:-comworker}" "${database:-comworker_platform}" > "$backup_dir/database.dump"
  [ -s "$backup_dir/database.dump" ] || die "数据库备份为空"

  cp "$source_root/.env" "$config_dir/.env"
  cp -a "$source_root/secrets/." "$config_dir/secrets/"
  [ ! -f "$source_root/.comworker-install.json" ] || cp "$source_root/.comworker-install.json" "$config_dir/.comworker-install.json"
  [ ! -f "$source_root/manifest.json" ] || cp "$source_root/manifest.json" "$config_dir/release-manifest.json"

  local volumes=()
  for volume in comworker-offline-platform-data comworker-offline-platform-skills comworker-offline-builtin-skills; do
    if docker volume inspect "$volume" >/dev/null 2>&1; then
      volumes+=("$volume")
    fi
  done
  while IFS= read -r volume; do
    [ -z "$volume" ] || volumes+=("$volume")
  done < <(docker volume ls --format '{{.Name}}' | grep '^hermes-data-' || true)
  for volume in "${volumes[@]}"; do
    printf '==> 备份数据卷 %s\n' "$volume"
    archive_volume "$volume" "$volume_dir"
  done

  version="$(source_version "$source_root")"
  created_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  source_json="$(printf '%s' "$source_root" | sed 's/\\/\\\\/g; s/"/\\"/g')"
  {
    printf '{\n'
    printf '  "product": "ComWorker",\n'
    printf '  "version": "%s",\n' "$version"
    printf '  "install_dir": "%s",\n' "$source_json"
    printf '  "created_at": "%s",\n' "$created_at"
    printf '  "database": "database.dump",\n'
    printf '  "volumes": ['
    local first=1
    for volume in "${volumes[@]}"; do
      [ "$first" -eq 1 ] || printf ', '
      printf '"%s"' "$volume"
      first=0
    done
    printf ']\n}\n'
  } > "$backup_dir/backup-manifest.json"

  (
    cd "$backup_dir"
    find database.dump config volumes backup-manifest.json -type f | LC_ALL=C sort | while IFS= read -r path; do
      sha256sum "$path"
    done > checksums.sha256
    sha256sum -c checksums.sha256 >/dev/null
  )
  printf '备份完成: %s\n' "$backup_dir"
}

confirm_restore() {
  printf '恢复会覆盖当前数据库和数据。请输入 RESTORE 确认: '
  read -r answer
  [ "$answer" = RESTORE ] || die "已取消"
}

verify_backup() {
  local backup_dir="$1"
  [ -d "$backup_dir" ] || die "备份目录不存在: $backup_dir"
  [ -f "$backup_dir/backup-manifest.json" ] || die "备份缺少 backup-manifest.json"
  [ -f "$backup_dir/checksums.sha256" ] || die "备份缺少 checksums.sha256"
  [ -s "$backup_dir/database.dump" ] || die "备份缺少 database.dump"
  (cd "$backup_dir" && sha256sum -c checksums.sha256 >/dev/null) || die "备份校验失败"
}

restore_volume() {
  local volume="$1" archive="$2" archive_dir
  archive_dir="$(cd "$(dirname "$archive")" && pwd)"
  docker volume inspect "$volume" >/dev/null 2>&1 || docker volume create "$volume" >/dev/null
  while IFS= read -r container_id; do
    [ -z "$container_id" ] || docker stop "$container_id" >/dev/null
  done < <(docker ps -q --filter "volume=$volume")
  docker run --rm \
    -e ARCHIVE_NAME="$(basename "$archive")" \
    -v "$volume:/target" \
    -v "$archive_dir:/backup:ro" \
    --entrypoint sh "$HELPER_IMAGE" -c \
    'set -eu; find /target -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +; zstd -dc "/backup/$ARCHIVE_NAME" | tar -C /target -xf -'
}

restore_all() {
  local backup_dir="$1" start_after="${2:-yes}"
  backup_dir="$(cd "$backup_dir" && pwd)"
  verify_backup "$backup_dir"
  confirm_restore
  "${COMPOSE[@]}" down --remove-orphans

  local archive volume
  for archive in "$backup_dir"/volumes/*.tar.zst; do
    [ -e "$archive" ] || continue
    volume="$(basename "$archive" .tar.zst)"
    printf '==> 恢复数据卷 %s\n' "$volume"
    restore_volume "$volume" "$archive"
  done

  [ -f "$backup_dir/config/.env" ] || die "备份缺少配置文件"
  cp "$backup_dir/config/.env" "$ROOT/.env"
  mkdir -p "$ROOT/secrets"
  cp -a "$backup_dir/config/secrets/." "$ROOT/secrets/"
  if [ -f "$backup_dir/config/.comworker-install.json" ]; then
    cp "$backup_dir/config/.comworker-install.json" "$ROOT/.comworker-install.json"
  fi
  load_admin_password "$ROOT"

  local user database deadline
  user="$(env_value_from "$ROOT" POSTGRES_USER)"
  database="$(env_value_from "$ROOT" POSTGRES_DB)"
  "${COMPOSE[@]}" up -d postgres
  deadline=$((SECONDS + 90))
  until "${COMPOSE[@]}" exec -T postgres pg_isready -U "${user:-comworker}" >/dev/null 2>&1; do
    [ "$SECONDS" -lt "$deadline" ] || die "数据库恢复前未能就绪"
    sleep 2
  done
  "${COMPOSE[@]}" exec -T postgres dropdb --if-exists --force -U "${user:-comworker}" "${database:-comworker_platform}"
  "${COMPOSE[@]}" exec -T postgres createdb -U "${user:-comworker}" "${database:-comworker_platform}"
  "${COMPOSE[@]}" exec -T postgres pg_restore --exit-on-error -U "${user:-comworker}" \
    -d "${database:-comworker_platform}" < "$backup_dir/database.dump"

  if [ "$start_after" = yes ]; then
    "${COMPOSE[@]}" up -d
    printf '恢复完成: %s\n' "$backup_dir"
  fi
}

rollback_all() {
  local backup_dir="$1" old_root
  backup_dir="$(cd "$backup_dir" && pwd)"
  old_root="$(sed -n 's/^[[:space:]]*"install_dir":[[:space:]]*"\([^"]*\)".*/\1/p' "$backup_dir/backup-manifest.json" | head -n 1)"
  [ -n "$old_root" ] && [ -f "$old_root/compose.yml" ] || die "备份记录的旧版本目录不可用"
  restore_all "$backup_dir" no
  load_admin_password "$old_root"
  docker compose -p comworker-offline --env-file "$old_root/.env" -f "$old_root/compose.yml" up -d
  printf '回滚完成，已启动旧版本目录: %s\n' "$old_root"
}

reset_admin_password() {
  local newpw="${1:-}"
  [ -z "$newpw" ] && newpw="$(head -c 12 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 16)"
  [ -n "$newpw" ] || die "无法生成随机密码"
  "${COMPOSE[@]}" ps -q gateway >/dev/null 2>&1 || die "请先启动 ComWorker（./comworker.sh start）再重置密码"
  local script
  script='
import asyncio, os
from app.db.engine import async_session
from app.db.models import User
from app.auth.service import hash_password
from sqlalchemy import select

async def main():
    npw = os.environ["NEW_PW"]
    async with async_session() as s:
        u = (await s.execute(select(User).where(User.username == "admin"))).scalar_one_or_none()
        if not u:
            print("ERROR: 未找到 admin 账户"); raise SystemExit(1)
        u.password_hash = hash_password(npw)
        u.must_change_password = True
        await s.commit()
    print("管理员密码已重置。")

asyncio.run(main())
'
  NEW_PW="$newpw" "${COMPOSE[@]}" exec -T -e NEW_PW gateway python -c "$script" || die "重置失败"
  printf '%s\n' "$newpw" > "$ROOT/secrets/admin_password"
  chmod 600 "$ROOT/secrets/admin_password"
  printf '管理员密码已重置并保存到 secrets/admin_password：%s\n' "$newpw"
}

case "$action" in
  status) "${COMPOSE[@]}" ps ;;
  start) "${COMPOSE[@]}" up -d ;;
  stop) "${COMPOSE[@]}" stop ;;
  restart) "${COMPOSE[@]}" restart ;;
  logs) "${COMPOSE[@]}" logs -f --tail=200 ;;
  backup) backup_all ;;
  upgrade)
    upgrade_package="${2:?请填写已解压的新版本安装目录}"
    upgrade_package="$(cd "$upgrade_package" && pwd)" || die "新版本安装目录不存在"
    [ -x "$upgrade_package/install.sh" ] || die "新版本目录缺少可执行的 install.sh"
    BACKUP_SOURCE_DIR="$ROOT" backup_all
    COMWORKER_BACKUP_DONE=1 exec "$upgrade_package/install.sh" --upgrade-from "$ROOT"
    ;;
  restore)
    backup_path="${2:?请填写备份目录}"
    restore_all "$backup_path"
    ;;
  rollback)
    backup_path="${2:?请填写升级前备份目录}"
    rollback_all "$backup_path"
    ;;
  reset-admin-password)
    reset_admin_password "${2:-}"
    ;;
  uninstall)
    if [ "${2:-}" = "--purge" ]; then
      printf '这将永久删除数据库和所有用户数据。请输入 DELETE 确认: '
      read -r answer
      [ "$answer" = DELETE ] || die "已取消"
      "${COMPOSE[@]}" down -v --remove-orphans
    else
      "${COMPOSE[@]}" down --remove-orphans
      echo '容器已移除，数据卷和密钥已保留。'
    fi
    ;;
  *)
    echo '用法: ./comworker.sh status|start|stop|restart|logs|backup|upgrade <新目录>|restore <备份目录>|rollback <备份目录>|reset-admin-password [新密码]|uninstall [--purge]'
    exit 2
    ;;
esac
