#!/bin/bash
# Mumu 客户端 GitHub 上传脚本
# 用法: ./upload_to_github.sh YOUR_GITHUB_USERNAME

set -e

# 检查参数
if [ -z "$1" ]; then
    echo "❌ 错误: 请提供 GitHub 用户名"
    echo "用法: ./upload_to_github.sh your_username"
    exit 1
fi

USERNAME=$1
REPO_NAME="mumu-client"

echo "========================================"
echo "  Mumu 客户端 GitHub 上传脚本"
echo "========================================"
echo ""
echo "GitHub 用户名: $USERNAME"
echo "仓库名: $REPO_NAME"
echo ""

# 检查 git 是否安装
if ! command -v git &> /dev/null; then
    echo "❌ 错误: Git 未安装"
    echo "请先安装 Git: https://git-scm.com/downloads"
    exit 1
fi

# 检查是否在正确的目录
if [ ! -f "client.py" ]; then
    echo "❌ 错误: 当前目录不是 mumu-client 项目根目录"
    echo "请切换到项目目录后运行此脚本"
    exit 1
fi

echo "✅ Git 已安装"
echo "✅ 当前目录正确"
echo ""

# 初始化 git 仓库
if [ ! -d ".git" ]; then
    echo "📦 初始化 Git 仓库..."
    git init
else
    echo "📦 Git 仓库已存在"
fi

# 添加文件
echo "📦 添加文件到暂存区..."
git add .

# 提交
echo "📦 创建提交..."
if git commit -m "Initial commit: Mumu client v2.0

核心功能：
- AI 画像分析（性格、兴趣、发展特征）
- 照片管理与日历浏览
- 自动生成成长日志
- 支持多厂商 AI 接口配置

支持 AI 服务商：OpenAI、阿里云、DeepSeek、豆包"; then
    echo "✅ 提交成功"
else
    echo "⚠️  没有新文件需要提交"
fi

# 检查远程仓库是否已配置
if git remote | grep -q "origin"; then
    echo "📦 远程仓库已配置"
else
    echo "📦 配置远程仓库..."
    git remote add origin "https://github.com/$USERNAME/$REPO_NAME.git"
fi

# 设置分支名
echo "📦 设置分支名为 main..."
git branch -M main

# 推送
echo ""
echo "🚀 推送到 GitHub..."
echo "提示: 如果要求输入密码，请使用 GitHub Personal Access Token"
echo ""

if git push -u origin main; then
    echo ""
    echo "========================================"
    echo "  ✅ 上传成功！"
    echo "========================================"
    echo ""
    echo "仓库地址: https://github.com/$USERNAME/$REPO_NAME"
    echo ""
else
    echo ""
    echo "❌ 推送失败"
    echo ""
    echo "可能的原因:"
    echo "1. 仓库不存在 - 请先在 GitHub 创建空仓库"
    echo "2. 认证失败 - 请检查用户名或使用 Token"
    echo "3. 网络问题 - 请检查网络连接"
    echo ""
    echo "创建仓库步骤:"
    echo "1. 访问 https://github.com/new"
    echo "2. 输入 Repository name: $REPO_NAME"
    echo "3. 选择 Public 或 Private"
    echo "4. 不要勾选 README 或 .gitignore"
    echo "5. 点击 Create repository"
    exit 1
fi
