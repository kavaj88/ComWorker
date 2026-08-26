#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
COMPOSE=(docker compose -p comworker-offline -f compose.yml)
action="${1:-status}"

if [ -s secrets/admin_password ]; then
  export ADMIN_PASSWORD="$(cat secrets/admin_password)"
fi

env_value() { sed -n "s/^$1=//p" .env | head -n 1; }

backup() {
  mkdir -p backups
  stamp="$(date +%Y%m%d-%H%M%S)"
  user="$(env_value POSTGRES_USER)"
  database="$(env_value POSTGRES_DB)"
  "${COMPOSE[@]}" exec -T postgres pg_dump -U "${user:-comworker}" "${database:-comworker_platform}" \
    > "backups/database-$stamp.sql"
  tar -czf "backups/config-$stamp.tar.gz" .env secrets
  printf '备份完成: backups/*-%s.*\n' "$stamp"
}

case "$action" in
  status) "${COMPOSE[@]}" ps ;;
  start) "${COMPOSE[@]}" up -d ;;
  stop) "${COMPOSE[@]}" stop ;;
  restart) "${COMPOSE[@]}" restart ;;
  logs) "${COMPOSE[@]}" logs -f --tail=200 ;;
  backup) backup ;;
  upgrade) backup; exec "$ROOT/install.sh" ;;
  uninstall)
    if [ "${2:-}" = "--purge" ]; then
      printf '这将永久删除数据库和所有用户数据。请输入 DELETE 确认: '
      read -r answer
      [ "$answer" = DELETE ] || { echo '已取消'; exit 1; }
      "${COMPOSE[@]}" down -v --remove-orphans
    else
      "${COMPOSE[@]}" down --remove-orphans
      echo '容器已移除，数据卷和密钥已保留。'
    fi
    ;;
  *)
    echo '用法: ./comworker.sh status|start|stop|restart|logs|backup|upgrade|uninstall [--purge]'
    exit 2
    ;;
esac
