#!/usr/bin/env bash
# ============================================================
# 现实模拟 RealitySim - Linux 一键安装脚本
# 用法: wget -O install.sh https://dl.xianshimoni.com/install/install.sh && bash install.sh
# 支持: Ubuntu / Debian / CentOS / Fedora / Arch 等 x86_64 发行版
# ============================================================
set -e

PRODUCT="现实模拟 RealitySim"
VERSION="v1.1.0"
PRIMARY_URL="https://dl.xianshimoni.com/RealitySim_Linux_${VERSION}.tar.gz"
FALLBACK_URL="https://github.com/231ljk/realitysim-test/releases/download/${VERSION}/RealitySim_Linux.tar.gz"
INSTALL_DIR="$HOME/.local/share/RealitySim"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$HOME/.local/share/applications"
TMP_TAR="$(mktemp /tmp/realitysim.XXXXXX.tar.gz)"

echo ""
echo "======================================"
echo "  ${PRODUCT} 一键安装 (${VERSION})"
echo "======================================"
echo ""

# 架构检查
ARCH="$(uname -m)"
if [ "$ARCH" != "x86_64" ]; then
    echo "错误：当前仅支持 x86_64 架构（检测到 $ARCH）" >&2
    exit 1
fi

# 依赖检查
command -v curl >/dev/null 2>&1 || command -v wget >/dev/null 2>&1 || { echo "错误：需要 curl 或 wget"; exit 1; }
command -v tar  >/dev/null 2>&1 || { echo "错误：需要 tar"; exit 1; }

download() {
    local url="$1" out="$2"
    echo "正在下载: $url"
    if command -v curl >/dev/null 2>&1; then
        curl -fSL --retry 3 -o "$out" "$url"
    else
        wget -O "$out" "$url"
    fi
}

# 下载安装包（官方域名优先，GitHub 兜底）
if ! download "$PRIMARY_URL" "$TMP_TAR" 2>/dev/null; then
    echo "官方域名下载失败，尝试备用镜像..." >&2
    download "$FALLBACK_URL" "$TMP_TAR"
fi

SIZE=$(stat -c%s "$TMP_TAR" 2>/dev/null || stat -f%z "$TMP_TAR")
if [ "$SIZE" -lt 1000000 ]; then
    echo "错误：下载文件异常（$(echo "scale=1; $SIZE/1048576" | bc)MB），疑似被拦截" >&2
    rm -f "$TMP_TAR"
    exit 1
fi

echo "下载完成（$(echo "scale=1; $SIZE/1048576" | bc) MB），解压安装中..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$APP_DIR"
tar -xzf "$TMP_TAR" -C "$INSTALL_DIR" --strip-components=1
rm -f "$TMP_TAR"

# 可执行权限
chmod +x "$INSTALL_DIR/RealitySim.x86_64" 2>/dev/null || true

# 启动器
cat > "$BIN_DIR/realitysim" <<EOF
#!/usr/bin/env bash
exec "$INSTALL_DIR/RealitySim.x86_64" "\$@"
EOF
chmod +x "$BIN_DIR/realitysim"

# 桌面快捷方式
cat > "$APP_DIR/realitysim.desktop" <<EOF
[Desktop Entry]
Name=现实模拟
Name[en]=RealitySim
Comment=1:1 还原现实的沙盒人生模拟游戏
Exec=$INSTALL_DIR/RealitySim.x86_64
Icon=applications-games
Terminal=false
Type=Application
Categories=Game;
StartupNotify=true
EOF

# 建议 PATH
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) echo "提示：$BIN_DIR 不在 PATH，可执行: echo 'export PATH=\"\$HOME/.local/bin:\$PATH\"' >> ~/.bashrc" ;;
esac

echo ""
echo "✔ ${PRODUCT} 安装成功！" 
echo "  安装位置: $INSTALL_DIR"
echo "  启动方式: 在应用菜单搜索「现实模拟」，或终端执行 realitysim"
echo ""
echo "  建议加入官方社区：https://231ljk.github.io/realitysim-test/"
echo ""
