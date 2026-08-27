#!/bin/bash
# GPS 平台自动备份：PostgreSQL 数据库 + 上传文件 + 配置，保留最近 14 天
# 用法：
#   手动执行：bash backup.sh
#   定时执行：crontab -e  →  0 3 * * * /opt/gps-tracker/backup.sh >> /var/log/gps-backup.log 2>&1
set -euo pipefail

BACKUP_DIR=/opt/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d_%H%M%S)
# Docker Compose 项目名（取决于启动目录名，默认为目录名 dome；若不同请修改此变量）
COMPOSE_PROJECT=dome

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%F %T')] 开始备份..."

# 1. 备份 PostgreSQL（主数据源，最重要）
echo "[$(date '+%F %T')] 正在导出 PostgreSQL..."
docker exec tracker-postgres pg_dump \
    -U "${POSTGRES_USER:-gps}" \
    -d "${POSTGRES_DB:-gps}" \
    --no-password \
    | gzip > "$BACKUP_DIR/pg_${STAMP}.sql.gz"
echo "[$(date '+%F %T')] PostgreSQL 备份完成: pg_${STAMP}.sql.gz ($(du -sh "$BACKUP_DIR/pg_${STAMP}.sql.gz" | cut -f1))"

# 2. 备份上传文件（头像/Logo 等，存在 uploads_data volume 中）
echo "[$(date '+%F %T')] 正在备份上传文件..."
docker run --rm \
    -v "${COMPOSE_PROJECT}_uploads_data:/uploads:ro" \
    -v "$BACKUP_DIR":/backup \
    alpine \
    tar -czf "/backup/uploads_${STAMP}.tar.gz" -C /uploads . 2>/dev/null \
    || echo "[警告] 上传文件 volume 暂无数据或卷名不匹配（期望: ${COMPOSE_PROJECT}_uploads_data）"

# 3. 备份 compose 配置（脱敏：只备份结构，不包含 .env 敏感值）
#    .env 文件含密钥，不应出现在备份包里——使用方自行保管密钥
cp /opt/gps-tracker/docker-compose.yml "$BACKUP_DIR/compose_${STAMP}.yml" 2>/dev/null \
    || echo "[警告] docker-compose.yml 不在 /opt/gps-tracker/，跳过配置备份"

# 4. 打包（PG dump + 上传文件 + compose）
cd "$BACKUP_DIR"
PACK_FILES=("pg_${STAMP}.sql.gz")
[[ -f "uploads_${STAMP}.tar.gz" ]] && PACK_FILES+=("uploads_${STAMP}.tar.gz")
[[ -f "compose_${STAMP}.yml"    ]] && PACK_FILES+=("compose_${STAMP}.yml")
tar -czf "backup_${STAMP}.tar.gz" "${PACK_FILES[@]}"
rm -f "pg_${STAMP}.sql.gz" "uploads_${STAMP}.tar.gz" "compose_${STAMP}.yml"

# 5. 删除超过 KEEP_DAYS 天的旧备份
find "$BACKUP_DIR" -name "backup_*.tar.gz" -mtime +"${KEEP_DAYS}" -delete

echo "[$(date '+%F %T')] 备份完成: backup_${STAMP}.tar.gz ($(du -sh "$BACKUP_DIR/backup_${STAMP}.tar.gz" | cut -f1))"
echo "[$(date '+%F %T')] 最近备份文件："
ls -lh "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | tail -5

# 6. 恢复说明（写在脚本里方便查阅）
# 恢复 PostgreSQL：
#   gunzip -c backup_YYYYMMDD_HHMMSS.tar.gz | tar -xO pg_*.sql.gz | gunzip | \
#     docker exec -i tracker-postgres psql -U gps -d gps
# 恢复上传文件：
#   tar -xzf backup_YYYYMMDD_HHMMSS.tar.gz uploads_*.tar.gz -O | \
#     docker run --rm -i -v dome_uploads_data:/uploads alpine tar -xz -C /uploads
