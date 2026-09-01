#!/bin/sh
# 容器以 root 启动：先确保挂载卷(uploads/data)属主为 appuser，再切到 appuser 运行应用。
# 解决具名卷首次创建时属主为 root、导致 appuser 无法写入上传目录(Logo/头像上传 500)的问题。
# 每次启动都会执行，重建容器/卷后依然自动修复，无需人工干预。
chown -R appuser:appuser /app/uploads /app/data 2>/dev/null || true
chmod -R 775 /app/uploads 2>/dev/null || true
exec runuser -u appuser -- gunicorn --config gunicorn.conf.py app:app
