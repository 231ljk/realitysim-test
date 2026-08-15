# ============================================================
# 现实模拟 RealitySim - Windows 一键安装脚本
# 用法: irm https://dl.xianshimoni.com/install.ps1 | iex
# ============================================================
$ErrorActionPreference = 'Stop'
$ProductName = '现实模拟 RealitySim'

Write-Host ""
Write-Host "======================================" -ForegroundColor Green
Write-Host "  $ProductName 一键安装" -ForegroundColor Green
Write-Host "======================================" -ForegroundColor Green
Write-Host ""

# 下载源：官方域名优先，GitHub Releases 兜底
$PrimaryUrl  = "https://dl.xianshimoni.com/RealitySim_Setup_v1.1.0.exe"
$FallbackUrl = "https://github.com/231ljk/realitysim-test/releases/download/v1.1.0/RealitySim_Setup_v1.1.0.exe"
$Installer   = Join-Path $env:TEMP "RealitySim_Setup_v1.1.0.exe"
$MinSize     = 10MB   # 小于 10MB 视为下载失败/被劫持

function Download-File {
    param([string]$Url, [string]$Out)
    Write-Host "正在下载: $Url" -ForegroundColor Cyan
    $ProgressPreference = 'SilentlyContinue'
    Invoke-WebRequest -Uri $Url -OutFile $Out -UseBasicParsing
    $ProgressPreference = 'Continue'
    $size = (Get-Item $Out).Length
    if ($size -lt $MinSize) { throw "下载文件异常（$([math]::Round($size/1MB,1))MB），疑似被拦截" }
    return $size
}

$ok = $false
try {
    $size = Download-File -Url $PrimaryUrl -Out $Installer
    $ok = $true
} catch {
    Write-Host "官方域名下载失败: $($_.Exception.Message)" -ForegroundColor Yellow
    Write-Host "尝试备用镜像..." -ForegroundColor Yellow
    try {
        $size = Download-File -Url $FallbackUrl -Out $Installer
        $ok = $true
    } catch {
        Write-Host "备用镜像也失败: $($_.Exception.Message)" -ForegroundColor Red
    }
}

if (-not $ok) {
    Write-Host "安装失败：无法下载安装包，请检查网络后重试。" -ForegroundColor Red
    exit 1
}

Write-Host "下载完成（$([math]::Round($size/1MB,1)) MB），开始安装..." -ForegroundColor Cyan

# 启动安装向导（如需静默安装可加 /S）
$p = Start-Process -FilePath $Installer -ArgumentList '/S' -Wait -PassThru
if ($p.ExitCode -eq 0) {
    Write-Host ""
    Write-Host "✔ $ProductName 安装成功！" -ForegroundColor Green
    Write-Host "  已创建桌面快捷方式「现实模拟」，也可以从开始菜单启动。" -ForegroundColor Green
    Write-Host ""
    Write-Host "  建议加入官方社区：https://231ljk.github.io/realitysim-test/" -ForegroundColor Cyan
} else {
    Write-Host "安装程序返回异常代码 $($p.ExitCode)，请手动运行 $Installer 完成安装。" -ForegroundColor Yellow
}
Write-Host ""
