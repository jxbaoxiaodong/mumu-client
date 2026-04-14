#!/bin/bash
# Mumu Client Release 构建脚本 - 完整版
# 用法: ./release.sh <tag版本号> [commit信息]
# 示例: ./release.sh v32 "fix: 修复xxx问题"
# 需要设置环境变量: GITEE_TOKEN (Gitee 私人令牌)

set -euo pipefail

# 加载环境变量（如果存在 .env 文件）
if [ -f "/home/bob/projects/mumu/.env" ]; then
    export $(grep -v '^#' /home/bob/projects/mumu/.env | xargs)
fi

TAG=${1:-v32}
COMMIT_MSG=${2:-"release: 新版本发布"}
MUMU_DIR="/home/bob/projects/mumu"
CLIENT_DIR="/home/bob/projects/mumu-client"
DOWNLOAD_DIR="$MUMU_DIR/landing_page/download"
LANDING_INDEX_FILE="$MUMU_DIR/landing_page/index.html"
LANDING_DOWNLOAD_BASE_URL="${LANDING_DOWNLOAD_BASE_URL:-https://mumu.ftir.fun/download}"
REQUIRED_ASSETS=("mumu-windows.exe" "mumu-linux" "mumu-macos")

public_asset_name() {
    local name="$1"
    local tag="${2:-$TAG}"
    case "$name" in
        *.exe)
            printf '%s-%s.exe' "${name%.exe}" "$tag"
            ;;
        *)
            printf '%s-%s' "$name" "$tag"
            ;;
    esac
}

landing_cache_token() {
    python3 - "$DOWNLOAD_DIR" "${REQUIRED_ASSETS[@]}" <<'PY'
from datetime import datetime
from pathlib import Path
import sys

download_dir = Path(sys.argv[1])
timestamps = []
for name in sys.argv[2:]:
    asset_path = download_dir / name
    if asset_path.exists():
        timestamps.append(asset_path.stat().st_mtime)

if not timestamps:
    print("", end="")
    raise SystemExit(0)

print(datetime.fromtimestamp(max(timestamps)).strftime("%Y%m%d%H%M%S"), end="")
PY
}

sync_landing_public_assets() {
    mkdir -p "$DOWNLOAD_DIR"
    local name source_file public_name public_path
    for name in "${REQUIRED_ASSETS[@]}"; do
        source_file="$DOWNLOAD_DIR/$name"
        [[ -f "$source_file" ]] || continue
        public_name="$(public_asset_name "$name")"
        public_path="$DOWNLOAD_DIR/$public_name"
        ln -sfn "$name" "$public_path"
    done
}

render_local_download_buttons() {
    local cache_token="${1:-$TAG}"
    python3 - "$DOWNLOAD_DIR" "$TAG" "$cache_token" "${REQUIRED_ASSETS[@]}" <<'PY'
from pathlib import Path
import sys

download_dir = Path(sys.argv[1])
tag = sys.argv[2]
cache_token = sys.argv[3]
assets = sys.argv[4:]

meta = {
    "mumu-windows.exe": (
        "Windows 版下载",
        "本站镜像，优先推荐，支持 Windows 10/11 64位系统",
    ),
    "mumu-linux": (
        "Linux 版下载",
        "本站镜像，优先推荐，支持 Ubuntu、Debian、CentOS 等主流发行版",
    ),
    "mumu-macos": (
        "macOS 版下载",
        "本站镜像，优先推荐，支持 macOS 10.15 及以上版本",
    ),
}

lines = []
for name in assets:
    if not (download_dir / name).is_file():
        continue

    if name.endswith(".exe"):
        public_name = f"{name[:-4]}-{tag}.exe"
    else:
        public_name = f"{name}-{tag}"

    title, desc = meta.get(name, (name, "本站镜像，优先推荐"))
    lines.extend(
        [
            f'                    <a href="/download/{public_name}?v={cache_token}" class="btn btn-local">',
            f"                        {title}",
            f"                        <span>{desc}</span>",
            "                    </a>",
        ]
    )

if not lines:
    lines = [
        '                    <div class="download-unavailable">',
        "                        当前本站镜像暂未就绪，请先使用下方备选下载入口。",
        "                    </div>",
    ]

print("\n".join(lines), end="")
PY
}

sync_landing_download_page() {
    [[ -f "$LANDING_INDEX_FILE" ]] || return 0

    local cache_token local_buttons_file
    cache_token="$(landing_cache_token)"
    if [ -z "$cache_token" ]; then
        cache_token="$(date '+%Y%m%d%H%M%S')"
    fi

    local_buttons_file="$(mktemp)"
    render_local_download_buttons "$cache_token" > "$local_buttons_file"

    python3 - "$LANDING_INDEX_FILE" "$TAG" "$local_buttons_file" <<'PY'
from pathlib import Path
import re
import sys

index_file = Path(sys.argv[1])
tag = sys.argv[2]
local_buttons_file = Path(sys.argv[3])
text = index_file.read_text(encoding="utf-8")
local_buttons = local_buttons_file.read_text(encoding="utf-8").rstrip()

start_marker = "<!-- LOCAL_DOWNLOAD_BUTTONS_START -->"
end_marker = "<!-- LOCAL_DOWNLOAD_BUTTONS_END -->"
if start_marker not in text or end_marker not in text:
    raise SystemExit("landing page markers are missing")

before, remainder = text.split(start_marker, 1)
_, after = remainder.split(end_marker, 1)
updated = before + start_marker + "\n" + local_buttons + "\n                    " + end_marker + after
def replace_version_label(match):
    suffix = match.group(2) or ""
    return f"<span>当前版本: {tag}{suffix}</span>"

updated, count = re.subn(
    r"<span>当前版本:\s*([^<]*?)(\s*/\s*Android 伴侣\s*[^<]+)?</span>",
    replace_version_label,
    updated,
    count=1,
)
if count != 1:
    updated, count = re.subn(
        r"(<div class=\"mini-card\">\s*<strong>)([^<]+)(</strong>\s*<span>电脑版当前版本</span>)",
        lambda match: f"{match.group(1)}{tag}{match.group(3)}",
        updated,
        count=1,
        flags=re.S,
    )
if count != 1:
    raise SystemExit("failed to update landing version label")

if updated != text:
    index_file.write_text(updated, encoding="utf-8")
PY

    rm -f "$local_buttons_file"
}

cleanup_download_dir() {
    mkdir -p "$DOWNLOAD_DIR"
    rm -f \
        "$DOWNLOAD_DIR/mumu-windows.exe" \
        "$DOWNLOAD_DIR/mumu-linux" \
        "$DOWNLOAD_DIR/mumu-macos" \
        "$DOWNLOAD_DIR"/mumu-windows-v*.exe \
        "$DOWNLOAD_DIR"/mumu-linux-v* \
        "$DOWNLOAD_DIR"/mumu-macos-v*
}

# 检查必需的 Token
if [ -z "$GITHUB_TOKEN" ]; then
    echo "❌ 错误: 未设置 GITHUB_TOKEN 环境变量"
    echo "请设置: export GITHUB_TOKEN=your_github_token"
    exit 1
fi

if [ -z "$GITEE_TOKEN" ]; then
    echo "⚠️  警告: 未设置 GITEE_TOKEN 环境变量，将跳过 Gitee 同步"
    echo "如需同步到 Gitee，请先设置: export GITEE_TOKEN=your_token"
fi

if [ -x "$MUMU_DIR/sync_to_client.sh" ]; then
    echo "=== 0. 同步最新代码从 mumu 主项目 ==="
    "$MUMU_DIR/sync_to_client.sh"
    echo ""
fi

cd "$CLIENT_DIR"

echo "=== 1. 提交代码并推送到 GitHub ==="
git add -A
git commit -m "$COMMIT_MSG

Co-authored-by: Qwen-Coder <qwen-coder@alibabacloud.com>" || echo "无新代码可提交"
git tag -d $TAG 2>/dev/null || true
git tag $TAG
git push origin main
git push origin $TAG

echo ""
echo "=== 2. 等待 GitHub Actions 构建完成 (最多10分钟) ==="
MAX_WAIT=600
INTERVAL=30
ELAPSED=0

while [ $ELAPSED -lt $MAX_WAIT ]; do
    STATUS=$(curl -s "https://api.github.com/repos/jxbaoxiaodong/mumu-client/actions/runs?per_page=1" \
        -H "Authorization: token $GITHUB_TOKEN" | \
        python3 -c "import json,sys; d=json.load(sys.stdin); r=d['workflow_runs'][0]; print(r['status']+'/'+str(r['conclusion'])) if r['head_branch']=='$TAG' else print('waiting')" 2>/dev/null || echo "waiting")

    if [ "$STATUS" = "completed/success" ]; then
        echo "✅ GitHub 构建成功!"
        break
    elif [ "$STATUS" = "completed/failure" ]; then
        echo "❌ GitHub 构建失败!"
        exit 1
    fi

    echo "[$ELAPSED/$MAX_WAIT] 状态: $STATUS"
    sleep $INTERVAL
    ELAPSED=$((ELAPSED + INTERVAL))
done

if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "⏰ 等待超时"
    exit 1
fi

echo ""
echo "=== 3. 下载安装包到本地 ==="
mkdir -p $DOWNLOAD_DIR

cd $DOWNLOAD_DIR

# 只清理桌面端旧产物，不碰 Android APK 与兼容链接
cleanup_download_dir

# 下载 Windows
echo "📥 下载 Windows..."
curl -L -o mumu-windows.exe "https://github.com/jxbaoxiaodong/mumu-client/releases/download/$TAG/mumu-windows.exe"
echo "✅ Windows 下载完成 ($(du -h mumu-windows.exe | cut -f1))"

# 下载 Linux
echo "📥 下载 Linux..."
curl -L -o mumu-linux "https://github.com/jxbaoxiaodong/mumu-client/releases/download/$TAG/mumu-linux"
echo "✅ Linux 下载完成 ($(du -h mumu-linux | cut -f1))"

# 下载 macOS
echo "📥 下载 macOS..."
curl -L -o mumu-macos "https://github.com/jxbaoxiaodong/mumu-client/releases/download/$TAG/mumu-macos"
echo "✅ macOS 下载完成 ($(du -h mumu-macos | cut -f1))"

sync_landing_public_assets
sync_landing_download_page

echo ""
echo "=== 4. 推送到 Gitee ==="

# 确保 Gitee remote URL 正确
GITEE_OWNER="baoxiaodong1"
GITEE_REPO="mumu-client"
GITEE_EXPECTED_URL="https://gitee.com/${GITEE_OWNER}/${GITEE_REPO}.git"
GITEE_CURRENT_URL=$(git remote get-url gitee 2>/dev/null || echo "")

if [ "$GITEE_CURRENT_URL" != "$GITEE_EXPECTED_URL" ] && [ -n "$GITEE_TOKEN" ]; then
    echo "🔧 修正 Gitee remote URL..."
    git remote set-url gitee "$GITEE_EXPECTED_URL"
    # 添加 token 到 URL
    git remote set-url gitee "https://${GITEE_TOKEN}@gitee.com/${GITEE_OWNER}/${GITEE_REPO}.git"
fi

cd "$CLIENT_DIR"
git push gitee main || echo "⚠️ Gitee main 推送失败"
git push gitee $TAG || echo "⚠️ Gitee tag 推送失败"

# Gitee Release 创建和上传
if [ -n "$GITEE_TOKEN" ]; then
    echo ""
    echo "=== 5. 创建 Gitee Release ==="
    
    OWNER="baoxiaodong1"
    REPO="mumu-client"
    REPO="mumu-client"
    
    # 检查是否已存在 Release
    EXISTING_RELEASE=$(curl -s "https://gitee.com/api/v5/repos/${OWNER}/${REPO}/releases/tags/${TAG}?access_token=${GITEE_TOKEN}" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
    
    if [ -n "$EXISTING_RELEASE" ] && [ "$EXISTING_RELEASE" != "None" ]; then
        echo "ℹ️ Gitee Release $TAG 已存在 (ID: $EXISTING_RELEASE)"
        RELEASE_ID=$EXISTING_RELEASE
    else
        # 创建 Release
        echo "📝 创建 Gitee Release..."
        CREATE_RESULT=$(curl -s -X POST \
            "https://gitee.com/api/v5/repos/${OWNER}/${REPO}/releases" \
            -H "Content-Type: application/json" \
            -d "{
                \"access_token\": \"${GITEE_TOKEN}\",
                \"tag_name\": \"${TAG}\",
                \"name\": \"${TAG}\",
                \"body\": \"${COMMIT_MSG}\",
                \"prerelease\": false,
                \"target_commitish\": \"master\"
            }")
        
        RELEASE_ID=$(echo "$CREATE_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('id',''))" 2>/dev/null || echo "")
        
        if [ -n "$RELEASE_ID" ] && [ "$RELEASE_ID" != "None" ]; then
            echo "✅ Gitee Release 创建成功 (ID: $RELEASE_ID)"
        else
            echo "⚠️ Gitee Release 创建失败: $CREATE_RESULT"
            RELEASE_ID=""
        fi
    fi
    
    # 上传附件
    if [ -n "$RELEASE_ID" ]; then
        echo ""
        echo "=== 6. 上传文件到 Gitee Release ==="
        
        upload_to_gitee() {
            local file=$1
            local filepath="$DOWNLOAD_DIR/$file"
            
            if [ ! -f "$filepath" ]; then
                echo "❌ 文件不存在: $filepath"
                return 1
            fi
            
            # 检查文件大小（Gitee 限制 100MB）
            FILE_SIZE=$(stat -c%s "$filepath" 2>/dev/null || stat -f%z "$filepath" 2>/dev/null || echo "0")
            if [ "$FILE_SIZE" -gt 104857600 ]; then
                echo "⚠️ $file 大小超过 100MB ($(($FILE_SIZE/1024/1024))MB)，跳过上传"
                echo "   建议：Linux 用户请从 GitHub 下载"
                return 1
            fi
            
            # 检查是否已存在同名附件，如存在则先删除
            EXISTING_ASSET=$(curl -s "https://gitee.com/api/v5/repos/${OWNER}/${REPO}/releases/${RELEASE_ID}/assets" \
                -H "access_token: ${GITEE_TOKEN}" | python3 -c "import json,sys; d=json.load(sys.stdin); print([a['id'] for a in d if a['name']=='${file}'])" 2>/dev/null || echo "")
            if [ -n "$EXISTING_ASSET" ] && [ "$EXISTING_ASSET" != "[]" ]; then
                echo "🗑️ 删除旧的 $file..."
                ASSET_ID=$(echo $EXISTING_ASSET | tr -d '[]')
                curl -s -X DELETE "https://gitee.com/api/v5/repos/${OWNER}/${REPO}/releases/assets/${ASSET_ID}" \
                    -H "access_token: ${GITEE_TOKEN}" || true
            fi
            
            echo "📤 上传 $file ($(($FILE_SIZE/1024/1024))MB)..."
            
            # Gitee 上传附件 API
            UPLOAD_RESULT=$(curl -s -X POST \
                "https://gitee.com/api/v5/repos/${OWNER}/${REPO}/releases/${RELEASE_ID}/attach_files" \
                -F "access_token=${GITEE_TOKEN}" \
                -F "file=@${filepath}")
            
            if echo "$UPLOAD_RESULT" | grep -q "\"id\"" 2>/dev/null; then
                echo "✅ $file 上传成功"
            else
                echo "⚠️ $file 上传失败: $(echo "$UPLOAD_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('message','未知错误'))" 2>/dev/null)"
            fi
        }
        
        upload_to_gitee "mumu-windows.exe"
        upload_to_gitee "mumu-linux"
        upload_to_gitee "mumu-macos"
    fi
    
    echo ""
    echo "🔗 Gitee Release: https://gitee.com/${OWNER}/${REPO}/releases/tag/${TAG}"
else
    echo ""
    echo "⚠️ 跳过 Gitee Release 创建（未设置 GITEE_TOKEN）"
    echo "请手动到 Gitee 创建 Release 并上传文件："
    echo "  1. 访问: https://gitee.com/baoxiaodong1/mumu-client/releases"
    echo "  2. 点击「新建发布」，选择标签 $TAG"
    echo "  3. 上传以下文件："
    echo "     - $DOWNLOAD_DIR/mumu-windows.exe"
    echo "     - $DOWNLOAD_DIR/mumu-linux"
    echo "     - $DOWNLOAD_DIR/mumu-macos"
fi

echo ""
echo "=========================================="
echo "✅ 发布完成!"
echo "=========================================="
echo "GitHub Release: https://github.com/jxbaoxiaodong/mumu-client/releases/tag/$TAG"
if [ -n "$GITEE_TOKEN" ]; then
    echo "Gitee Release:  https://gitee.com/baoxiaodong1/mumu-client/releases/tag/$TAG"
fi
echo "官网下载:       https://mumu.ftir.fun/download/"
echo ""
echo "📁 本地文件:"
ls -lh $DOWNLOAD_DIR
