#!/bin/bash
cd /opt/gps-tracker
LOG=/opt/gps-tracker/巡检报告.log
{
echo "========== 巡检 $(date '+%Y-%m-%d %H:%M:%S') =========="
echo "[容器状态]"; docker compose ps --format "{{.Name}}: {{.Status}}"
echo "[磁盘]"; df -h / | tail -1
echo "[数据库大小]"; docker exec tracker-postgres psql -U gps -d gps -tc "SELECT pg_size_pretty(pg_database_size('gps'));"
echo "[设备在线/离线]"; docker exec tracker-postgres psql -U gps -d gps -tc "SELECT presence_state||': '||count(*) FROM device GROUP BY presence_state;"
echo "[近3小时位置上报]"; docker exec tracker-postgres psql -U gps -d gps -tc "SELECT count(*) FROM location_record WHERE created_at > to_char(now()-interval '3 hour','YYYY-MM-DD HH24:MI:SS');"
echo "[后端近期报错]"; docker compose logs --tail=300 backend 2>&1 | grep -iE "error|traceback|exception" | tail -5
echo ""
} >> "$LOG" 2>&1
tail -n 1200 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
