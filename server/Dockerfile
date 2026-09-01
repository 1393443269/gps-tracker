FROM python:3.11-slim

WORKDIR /app

# 安装依赖（独立层，代码变动时不重新安装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 创建非特权用户，避免容器以 root 运行
RUN useradd -r -u 1001 -s /sbin/nologin appuser \
    && mkdir -p /app/data /app/uploads \
    && chown -R appuser:appuser /app

# 复制业务代码
COPY app.py protocol.py protocol_g618g.py protocol_zhiling.py geo_resolve.py gunicorn.conf.py ./
COPY entrypoint.sh ./
RUN chmod +x /app/entrypoint.sh
# 拆分出的模块包(common/ core/ 等);后续新增子包目录都要在此补上,否则容器内 import 失败
COPY common/ ./common/
COPY core/ ./core/

# 数据目录（SQLite 落盘，docker-compose 挂载卷到此处）
RUN mkdir -p /app/data

# REST API + Socket.IO
EXPOSE 8080
# JT/T 808 TCP（设备直连，明文 TCP，不走 Nginx）
EXPOSE 9090

# 生产：Gunicorn + gevent WebSocket worker（替换 Flask dev server）
# 若需本地调试可覆盖：docker run ... python app.py
ENTRYPOINT ["/app/entrypoint.sh"]
