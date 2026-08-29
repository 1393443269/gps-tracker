#!/bin/bash
# GPS 平台自动备份：PostgreSQL 数据库 + 上传文件 + 配置，保留最近 14 天
# 用法：
#   手动执行：bash backup.sh
#   定时执行：crontab -e  →  0 3 * * * /opt/gps-tracker/backup.sh >> /var/log/gps-backup.log 2>&1
set -euo pipefail

BACKUP_DIR=/opt/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d_%H%M%S)
# Docker Compose 项目名（Docker 卷名前缀 = 项目名，默认取 compose 启动目录名）。
# 服务器部署在 /opt/gps-tracker，故项目名为 gps-tracker，卷全名如
# gps-tracker_tracker_uploads。若你的启动目录不同，用 `docker volume ls` 查实际前缀后改此值。
COMPOSE_PROJECT=gps-tracker

mkdir -p "$BACKUP_DIR"

echo "[$(date '+%F %T')] 开始备份..."

# 1. 备份 PostgreSQL（主数据源，最重要）
# 库名/用户名强制来自 env，不设 :-gps 默认值——否则改库名后会连错库导致备份空库或失败而不自知。
: "${POSTGRES_USER:?[严重] 未设置 POSTGRES_USER，中止备份（请从 .env 注入，勿用默认值）}"
: "${POSTGRES_DB:?[严重] 未设置 POSTGRES_DB，中止备份（请从 .env 注入，勿用默认值）}"

echo "[$(date '+%F %T')] 正在导出 PostgreSQL..."
# 用 PIPESTATUS 捕获 pg_dump 的退出码：管道整体退出码是最后一个命令(gzip)的，
# 若只看 $? 会漏掉 pg_dump 失败（gzip 仍会成功地压出一个空/残缺文件）。
docker exec tracker-postgres pg_dump \
    -U "$POSTGRES_USER" \
    -d "$POSTGRES_DB" \
    --no-password \
    | gzip > "$BACKUP_DIR/pg_${STAMP}.sql.gz"
PG_RC=${PIPESTATUS[0]}
if [[ "$PG_RC" -ne 0 ]]; then
    echo "[严重] pg_dump 失败（退出码 $PG_RC），中止本次备份：不打包、不轮转旧备份。"
    rm -f "$BACKUP_DIR/pg_${STAMP}.sql.gz"
    exit 1
fi
# gzip 完整性校验：确认压缩包未截断/未损坏，避免"备份成功假象"。
if ! gunzip -t "$BACKUP_DIR/pg_${STAMP}.sql.gz" 2>/dev/null; then
    echo "[严重] pg_${STAMP}.sql.gz 完整性校验失败（gunzip -t），中止本次备份：不打包、不轮转旧备份。"
    rm -f "$BACKUP_DIR/pg_${STAMP}.sql.gz"
    exit 1
fi
echo "[$(date '+%F %T')] PostgreSQL 备份完成: pg_${STAMP}.sql.gz ($(du -sh "$BACKUP_DIR/pg_${STAMP}.sql.gz" | cut -f1))"

# 2. 备份上传文件（头像/Logo 等，存在 tracker_uploads volume 中）
#    卷全名 = 项目名前缀 + compose 里的卷名 tracker_uploads
UPLOADS_VOLUME="${COMPOSE_PROJECT}_tracker_uploads"
echo "[$(date '+%F %T')] 正在备份上传文件（卷: ${UPLOADS_VOLUME}）..."
if ! docker volume inspect "$UPLOADS_VOLUME" >/dev/null 2>&1; then
    echo "[严重] 上传文件卷 ${UPLOADS_VOLUME} 不存在！请用 'docker volume ls' 核对实际卷名并修正 COMPOSE_PROJECT。本次上传文件未备份。"
else
    docker run --rm \
        -v "${UPLOADS_VOLUME}:/uploads:ro" \
        -v "$BACKUP_DIR":/backup \
        alpine \
        tar -czf "/backup/uploads_${STAMP}.tar.gz" -C /uploads . 2>/dev/null \
        && echo "[$(date '+%F %T')] 上传文件备份完成: uploads_${STAMP}.tar.gz" \
        || echo "[严重] 上传文件打包失败（卷存在但读取出错），请检查。"
fi

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

# 6. 异地/离线备份（TODO：生产必须推异地！）
# ---------------------------------------------------------------------------
# 单机本地备份无法抵御磁盘损坏/整机丢失/勒索加密。生产环境必须把 backup_*.tar.gz
# 推送到异地对象存储（阿里云 OSS / S3 等）或另一台机器。
# 推送需要访问凭据（AK/SK 或 STS），不在本次代码修复范围，此处仅预留钩子。
# 启用方式：设置环境变量 REMOTE_BACKUP=1 并在下方补全实际推送命令。
# if [[ "${REMOTE_BACKUP:-0}" == "1" ]]; then
#     echo "[$(date '+%F %T')] 推送异地备份..."
#     # TODO: 接入 OSS/S3，例如：
#     #   ossutil cp "$BACKUP_DIR/backup_${STAMP}.tar.gz" oss://your-bucket/gps-backups/
#     #   aws s3 cp "$BACKUP_DIR/backup_${STAMP}.tar.gz" s3://your-bucket/gps-backups/
#     :
# fi

# 7. 恢复说明（写在脚本里方便查阅）
# 注意：tar 提取阶段不做 shell glob，`tar -xO pg_*.sql.gz` 的通配不会展开，恢复必然失败。
#      正确做法是先解包再处理，或对 GNU tar 显式加 --wildcards。
# 恢复 PostgreSQL：
#   # 方式一（推荐，先解包再恢复）：
#   tar xzf backup_YYYYMMDD_HHMMSS.tar.gz            # 解出 pg_*.sql.gz / uploads_*.tar.gz / compose_*.yml
#   gunzip -c pg_*.sql.gz | \
#     docker exec -i tracker-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
#   # 方式二（GNU tar，显式启用通配，直接从包内流式恢复）：
#   tar -xzO --wildcards 'pg_*.sql.gz' -f backup_YYYYMMDD_HHMMSS.tar.gz | gunzip | \
#     docker exec -i tracker-postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"
# 恢复上传文件：（卷名 = 项目名前缀 + tracker_uploads，与备份时一致）
#   tar xzf backup_YYYYMMDD_HHMMSS.tar.gz uploads_*.tar.gz      # 先解出 uploads_*.tar.gz
#   docker run --rm -i -v gps-tracker_tracker_uploads:/uploads alpine tar -xz -C /uploads \
#     < uploads_*.tar.gz
