#!/usr/bin/env bash
# =============================================================================
# 资产追踪平台  一键部署脚本（阿里云 ECS / 任意 Linux）
# 使用方法：chmod +x deploy.sh && ./deploy.sh
# =============================================================================
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"

echo ""
echo "╔═══════════════════════════════════════════════╗"
echo "║   资产追踪平台  一键部署  (Linux / 阿里云 ECS)  ║"
echo "╚═══════════════════════════════════════════════╝"
echo ""

# ── 1. 检查并安装 Docker ──────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    echo "▶ Docker 未安装，自动安装中（需要 root 权限）..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable --now docker
    echo "✓ Docker 安装完成"
else
    echo "✓ Docker $(docker --version | awk '{print $3}' | tr -d ',')"
fi

# ── 2. 选择 docker compose 命令（v2 Plugin 优先） ────────────────────────────
if docker compose version &>/dev/null 2>&1; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    echo "▶ 未检测到 docker compose，尝试安装 compose plugin..."
    if command -v apt-get &>/dev/null; then
        apt-get install -y docker-compose-plugin
    elif command -v yum &>/dev/null; then
        yum install -y docker-compose-plugin
    else
        echo "✗ 请手动安装 docker-compose-plugin 后重试"
        exit 1
    fi
    COMPOSE="docker compose"
fi
echo "✓ $($COMPOSE version 2>&1 | head -1)"

cd "$REPO_DIR"

# ── 3. 先构建镜像（不停机）─────────────────────────────────────────────────────
# 关键：先 build 再 down，缩短停机窗口并保留回滚能力。
# 若 build 失败（set -e 会在此 exit），旧容器仍在运行、未被销毁，服务不中断，
# 等同自动回滚——修好构建问题重跑本脚本即可。
# 手动回滚（如新版启动后异常）：git checkout <上一个可用提交> && ./deploy.sh
echo ""
echo "▶ 构建镜像（首次约 3~5 分钟，后续利用缓存会很快）..."
$COMPOSE build

# ── 4. 停止旧容器 ─────────────────────────────────────────────────────────────
# 镜像已构建成功，才停旧容器，把停机窗口压到最短。
echo ""
echo "▶ 停止旧容器（如有）..."
$COMPOSE down --remove-orphans 2>/dev/null || true

# ── 5. 启动所有服务 ───────────────────────────────────────────────────────────
echo ""
echo "▶ 启动所有服务..."
$COMPOSE up -d

# ── 6. 等待后端就绪 ───────────────────────────────────────────────────────────
echo ""
echo "▶ 等待后端启动（最长 60 秒）..."
for i in $(seq 1 20); do
    if docker exec tracker-backend \
        python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/api/sys/users')" \
        &>/dev/null 2>&1; then
        echo "✓ 后端已就绪"
        break
    fi
    printf "."
    sleep 3
done
echo ""

# ── 7. 获取公网 IP ─────────────────────────────────────────────────────────────
SERVER_IP=$(curl -sf --max-time 5 https://ifconfig.me 2>/dev/null \
         || curl -sf --max-time 5 http://checkip.amazonaws.com 2>/dev/null \
         || hostname -I 2>/dev/null | awk '{print $1}' \
         || echo "YOUR_SERVER_IP")

# ── 8. 输出访问信息 ───────────────────────────────────────────────────────────
echo ""
echo "╔═══════════════════════════════════════════════════╗"
echo "║                   部署成功！                      ║"
echo "╠═══════════════════════════════════════════════════╣"
printf "║  平台地址：http://%-32s║\n" "$SERVER_IP"
echo "║  默认账号：admin / admin123                       ║"
echo "║  808 端口：9090（GPS 设备 TCP 接入）              ║"
echo "╠═══════════════════════════════════════════════════╣"
echo "║  阿里云安全组须放行：                             ║"
echo "║    80/TCP  — HTTP 管理后台                        ║"
echo "║    9090/TCP — GPS 设备接入                        ║"
echo "╚═══════════════════════════════════════════════════╝"
echo ""
echo "常用命令："
echo "  查看实时日志：$COMPOSE logs -f"
echo "  停止平台：    $COMPOSE down"
echo "  重启后端：    docker restart tracker-backend"
