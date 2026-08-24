# =============================================================================
# 资产追踪平台  Windows 本地启动脚本
# 前提：已安装 Docker Desktop
# 使用方法：右键 -> 用 PowerShell 运行，或在终端执行 .\start.ps1
# =============================================================================

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Set-Location $root

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║   资产追踪平台  本地启动 (Windows)    ║" -ForegroundColor Cyan
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# 检查 Docker
try {
    $v = (docker --version 2>&1)
    Write-Host "✓ $v" -ForegroundColor Green
} catch {
    Write-Host "✗ 未检测到 Docker，请先安装 Docker Desktop" -ForegroundColor Red
    Write-Host "  下载：https://www.docker.com/products/docker-desktop"
    exit 1
}

Write-Host ""
Write-Host "▶ 停止旧容器..." -ForegroundColor Yellow
docker compose down --remove-orphans 2>&1 | Out-Null

Write-Host "▶ 构建镜像（首次约 3~5 分钟）..." -ForegroundColor Yellow
docker compose build

Write-Host "▶ 启动所有服务..." -ForegroundColor Yellow
docker compose up -d

Write-Host ""
Write-Host "╔══════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║           启动成功！                 ║" -ForegroundColor Green
Write-Host "╠══════════════════════════════════════╣" -ForegroundColor Green
Write-Host "║  平台地址：http://localhost          ║" -ForegroundColor White
Write-Host "║  默认账号：admin / admin123          ║" -ForegroundColor White
Write-Host "║  808 端口：9090                      ║" -ForegroundColor White
Write-Host "╚══════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "常用命令："
Write-Host "  查看日志：docker compose logs -f"
Write-Host "  停止平台：docker compose down"
