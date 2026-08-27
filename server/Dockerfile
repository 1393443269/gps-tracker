FROM python:3.11-slim

WORKDIR /app

# 安装依赖（独立层，代码变动时不重新安装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制业务代码
COPY app.py protocol.py protocol_g618g.py protocol_zhiling.py geo_resolve.py ./

# 数据目录（SQLite 落盘，docker-compose 挂载卷到此处）
RUN mkdir -p /app/data

# REST API + Socket.IO
EXPOSE 8080
# JT/T 808 TCP（设备直连，明文 TCP，不走 Nginx）
EXPOSE 9090

CMD ["python", "app.py"]
