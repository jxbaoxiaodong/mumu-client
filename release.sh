#!/bin/bash
# Mumu Client Release 构建脚本 - 完整版
# 用法: ./release.sh <tag版本号> [commit信息]
# 示例: ./release.sh v32 "fix: 修复xxx问题"
# 需要设置环境变量: GITEE_TOKEN (Gitee 私人令牌)

set -e

# 加载环境变量（如果存在 .env 文件）
if [ -f "/home/bob/projects/mumu/.env" ]; then
    export $(grep -v '^#' /home/bob/projects/mumu/.env | xargs)
fi

TAG=${1:-v32}
COMMIT_MSG=${2:-"release: 新版本发布"}

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

cd /home/bob/projects/mumu-client

echo "=== 1. 提交代码并推送到 GitHub ==="
git add -A
git commit -m "$COMMIT_MSG

Co-authored-by: Qwen-Coder <qwen-coder@alibabacloud.com>" || echo "无新代码可提交"
git tag -d $TAG 2>/dev/null || true
git tag $TAG
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
DOWNLOAD_DIR=/home/bob/projects/mumu/landing_page/download
mkdir -p $DOWNLOAD_DIR

cd $DOWNLOAD_DIR

# 清理旧文件
rm -f mumu-*

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

echo ""
echo "=== 4. 推送到 Gitee ==="
cd /home/bob/projects/mumu-client
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
