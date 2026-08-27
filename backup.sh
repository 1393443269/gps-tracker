#!/bin/bash
# GPS 平台自动备份：数据库 + 配置，保留最近 14 天
set -e
BACKUP_DIR=/opt/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

# 1. 从 Docker volume 里导出 SQLite 数据库（用临时容器读卷）
docker run --rm -v gps-tracker_tracker_data:/data -v "$BACKUP_DIR":/backup alpine \
  sh -c "cp /data/tracker.db /backup/tracker_${STAMP}.db" 2>/dev/null || \
  echo "[警告] 数据库文件暂不存在（设备还没上报过数据）"

# 2. 备份 compose 配置（含密钥）
cp /opt/gps-tracker/docker-compose.yml "$BACKUP_DIR/compose_${STAMP}.yml"

# 3. 打包压缩
cd "$BACKUP_DIR"
tar -czf "backup_${STAMP}.tar.gz" tracker_${STAMP}.db compose_${STAMP}.yml 2>/dev/null || \
  tar -czf "backup_${STAMP}.tar.gz" compose_${STAMP}.yml
rm -f tracker_${STAMP}.db compose_${STAMP}.yml

# 4. 删除超过 KEEP_DAYS 天的旧备份
find "$BACKUP_DIR" -name "backup_*.tar.gz" -mtime +${KEEP_DAYS} -delete

echo "[$(date '+%F %T')] 备份完成: backup_${STAMP}.tar.gz"
ls -lh "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | tail -5
