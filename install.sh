#!/bin/bash
# ======================================================================
# VoxCPM2 語音克隆 App — 一鍵安裝腳本
# 適用：Apple 晶片 Mac（M1/M2/M3/M4）
# 同事使用方式：見隨附的「安裝說明」
# ======================================================================

set -u  # 用到未定義變數才報錯；不使用 set -e（避免非致命的非零退出中斷整個流程）
trap '' PIPE  # 忽略 SIGPIPE，避免 pip 大量輸出時因管線（curl | bash）關閉而被默默終止

# 統一的錯誤處理：關鍵步驟失敗時，印出清楚訊息並停止
fail() {
    echo ""
    echo "❌ 安裝在這一步出問題了：$1"
    echo "   請把終端機從上到下的訊息截圖，傳給提供這個工具的人。"
    echo ""
    exit 1
}

# 帶重試的執行：網路類步驟最多試 3 次，避免一次波動就失敗
retry() {
    local n=1
    local max=3
    until "$@"; do
        if [ $n -ge $max ]; then
            return 1
        fi
        echo "   （第 $n 次沒成功，5 秒後重試...）"
        n=$((n + 1))
        sleep 5
    done
    return 0
}

RAW_BASE="https://raw.githubusercontent.com/kerich1n/VoxCPM2-App/main"
APP_DIR="$HOME/VoxCPM2-App"

echo ""
echo "=================================================="
echo "  VoxCPM2 語音克隆 App 安裝程式"
echo "=================================================="
echo ""

# ---------- 0. 確認是 Apple 晶片 ----------
if [ "$(uname -m)" != "arm64" ]; then
    echo "⚠️  這個安裝腳本是為 Apple 晶片（M 系列）Mac 設計的。"
    echo "    你的電腦看起來不是 Apple 晶片，可能無法正常運作。"
    echo "    如有疑問請聯絡提供這個工具的人。"
    exit 1
fi

# ---------- 1. 偵測 / 安裝 Homebrew ----------
if ! command -v brew >/dev/null 2>&1; then
    # 若 brew 在標準路徑存在但還沒載入 PATH，先載入
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
fi

if ! command -v brew >/dev/null 2>&1; then
    echo ""
    echo "❌ 還沒安裝 Homebrew，無法繼續。"
    echo ""
    echo "   Homebrew 需要你親自輸入密碼安裝，沒辦法在這個一行指令裡自動完成。"
    echo "   請先複製下面這一行，貼到終端機按 Enter，照畫面指示安裝 Homebrew："
    echo ""
    echo '   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'
    echo ""
    echo "   裝完 Homebrew 後（記得照它最後提示貼那兩行 eval/echo 指令），"
    echo "   再重新執行一次本安裝指令即可。"
    echo ""
    exit 1
else
    echo "✅ 已偵測到 Homebrew"
    eval "$(/opt/homebrew/bin/brew shellenv)" 2>/dev/null || true
fi

# ---------- 2. 安裝 Python 3.11 ----------
if ! brew list python@3.11 >/dev/null 2>&1; then
    echo "➡️  安裝 Python 3.11..."
    retry brew install python@3.11 || fail "安裝 Python 3.11 失敗（請確認網路後重跑安裝指令）"
else
    echo "✅ 已安裝 Python 3.11"
fi

# 找出 python3.11 的實際路徑
PY311="$(brew --prefix python@3.11)/bin/python3.11"
if [ ! -x "$PY311" ]; then
    # 後備：用 PATH 裡的
    PY311="python3.11"
fi

# ---------- 3. 安裝 FFmpeg ----------
if ! brew list ffmpeg >/dev/null 2>&1; then
    echo "➡️  安裝 FFmpeg（語音克隆降噪功能需要）..."
    retry brew install ffmpeg || fail "安裝 FFmpeg 失敗（請確認網路後重跑安裝指令）"
else
    echo "✅ 已安裝 FFmpeg"
fi

# ---------- 4. 建立專案資料夾 + venv ----------
echo "➡️  建立專案資料夾與 Python 環境..."
mkdir -p "$APP_DIR"

# 若已有 venv 但內容不完整（例如先前安裝中斷、或被 git 追蹤後拉壞），先砍掉重建
if [ -d "$APP_DIR/venv" ] && [ ! -f "$APP_DIR/venv/bin/activate" ]; then
    echo "   （偵測到不完整的舊環境，清掉重建...）"
    rm -rf "$APP_DIR/venv"
fi

if [ ! -d "$APP_DIR/venv" ]; then
    "$PY311" -m venv "$APP_DIR/venv" || fail "建立 Python 虛擬環境失敗"
fi

# 啟用 venv
# shellcheck disable=SC1091
source "$APP_DIR/venv/bin/activate" || fail "無法進入 Python 環境（venv 可能不完整，請重跑）"

# ---------- 5. 安裝 Python 套件 ----------
echo "➡️  安裝 VoxCPM2 與相關套件（會下載較多檔案，請耐心等待，約 10～30 分鐘）..."

# 用 python -m pip 升級 pip（比直接 pip 安全，不會因 pip 換掉自己而中斷）
python -m pip install -q --upgrade pip || echo "   （pip 升級略過，不影響後續）"

# 明確列出 App 實際 import 到的所有套件，不依賴「voxcpm 剛好幫忙帶進來」：
#   voxcpm    -> AI 語音模型本體
#   pywebview -> 把介面包成獨立桌面視窗
#   gradio    -> 介面框架
#   soundfile -> 讀寫音檔
#   numpy     -> 音訊數值處理
#   pyobjc    -> 設定 macOS Dock 圖示（voxapp.py 用到 AppKit）
if ! retry python -m pip install -q voxcpm pywebview gradio soundfile numpy pyobjc; then
    fail "安裝 Python 套件（可能是網路問題，請確認網路後重跑安裝指令）"
fi

# 逐一驗證每個必要套件都真的能載入，缺哪個就明確說是哪個，不繼續往下假裝成功
for mod in voxcpm webview gradio soundfile numpy; do
    if ! python -c "import $mod" >/dev/null 2>&1; then
        fail "套件「$mod」安裝後仍無法載入（請重跑安裝指令）"
    fi
done
# pyobjc 缺少只影響 Dock 圖示，不影響 App 運作，所以單獨檢查、只提醒不中斷
if ! python -c "import AppKit" >/dev/null 2>&1; then
    echo "   （提醒：pyobjc 似乎沒裝好，App 仍可正常使用，只是 Dock 圖示可能不會顯示）"
fi
echo "✅ 套件安裝完成並驗證成功"

# ---------- 6. 下載 App 程式碼（voxapp.py、圖示、啟動檔）----------
echo "➡️  下載 App 程式..."
if ! retry curl -fsSL "$RAW_BASE/voxapp.py" -o "$APP_DIR/voxapp.py"; then
    fail "下載 voxapp.py 失敗（請確認網路後重跑）"
fi
# 確認下載到的是有內容的程式檔，不是空檔或錯誤頁
if [ ! -s "$APP_DIR/voxapp.py" ]; then
    fail "voxapp.py 下載後是空的（請重跑安裝指令）"
fi

# 下載自動更新模組（voxapp.py 啟動時會 import updater，缺了會打不開）
if ! retry curl -fsSL "$RAW_BASE/updater.py" -o "$APP_DIR/updater.py"; then
    fail "下載 updater.py 失敗（請確認網路後重跑）"
fi
if [ ! -s "$APP_DIR/updater.py" ]; then
    fail "updater.py 下載後是空的（請重跑安裝指令）"
fi

# 下載版本號檔（僅自動更新比對版本用；缺少不影響 App 運作，所以失敗只提醒不中斷）
retry curl -fsSL "$RAW_BASE/version.txt" -o "$APP_DIR/version.txt" || \
    echo "   （版本號檔下載失敗，不影響使用，只是自動更新可能無法比對版本）"

retry curl -fsSL "$RAW_BASE/voxcpm_icon.png" -o "$APP_DIR/voxcpm_icon.png" || \
    echo "   （圖示下載失敗，App 仍可用，只是沒有自訂圖示）"

# ---------- 7. 自動產生桌面 App（用 osascript 建立 .app 啟動器）----------
echo "➡️  建立桌面 App..."
APP_BUNDLE="$HOME/Desktop/VoxCPM2.app"
rm -rf "$APP_BUNDLE"
mkdir -p "$APP_BUNDLE/Contents/MacOS"
mkdir -p "$APP_BUNDLE/Contents/Resources"

# 啟動腳本（.app 的本體）
cat > "$APP_BUNDLE/Contents/MacOS/VoxCPM2" << 'LAUNCHER'
#!/bin/bash
cd "$HOME/VoxCPM2-App"
source "$HOME/VoxCPM2-App/venv/bin/activate"
exec python "$HOME/VoxCPM2-App/voxapp.py"
LAUNCHER
chmod +x "$APP_BUNDLE/Contents/MacOS/VoxCPM2"

# Info.plist（App 的基本資訊）
cat > "$APP_BUNDLE/Contents/Info.plist" << 'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>VoxCPM2</string>
    <key>CFBundleExecutable</key>
    <string>VoxCPM2</string>
    <key>CFBundleIdentifier</key>
    <string>com.voxcpm2.app</string>
    <key>CFBundleVersion</key>
    <string>1.0</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleIconFile</key>
    <string>icon.icns</string>
</dict>
</plist>
PLIST

# 把 PNG 轉成 App 圖示（.icns）
ICON_SRC="$APP_DIR/voxcpm_icon.png"
if [ -f "$ICON_SRC" ]; then
    ICONSET="$(mktemp -d)/icon.iconset"
    mkdir -p "$ICONSET"
    for size in 16 32 128 256 512; do
        sips -z $size $size     "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null 2>&1 || true
        double=$((size * 2))
        sips -z $double $double "$ICON_SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null 2>&1 || true
    done
    iconutil -c icns "$ICONSET" -o "$APP_BUNDLE/Contents/Resources/icon.icns" 2>/dev/null || true
fi

echo ""
echo "=================================================="
echo "  ✅ 安裝完成！"
echo "=================================================="
echo ""
echo "  桌面上已出現「VoxCPM2」App。"
echo "  第一次打開：對著它【按右鍵 → 打開】，"
echo "  跳出警告時再點一次「打開」即可。"
echo ""
echo "  第一次啟動會自動下載 AI 模型（數 GB），"
echo "  請耐心等待視窗出現（可能需要 5～10 分鐘）。"
echo ""
