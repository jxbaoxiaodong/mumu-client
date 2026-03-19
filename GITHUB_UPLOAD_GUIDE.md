# GitHub 上传指南

## 准备工作

1. 确保您已经在 GitHub 创建了空仓库 `mumu-client`
2. 确保本地已安装 Git

## 方法一：使用 HTTPS（推荐新手）

在终端执行以下命令：

```bash
# 进入项目目录
cd /home/bob/projects/mumu/mumu-client

# 初始化 Git 仓库
git init

# 添加所有文件
git add .

# 提交
git commit -m "Initial commit: Mumu client v2.0

核心功能：
- AI 画像分析（性格、兴趣、发展特征）
- 照片管理与日历浏览
- 自动生成成长日志
- 支持多厂商 AI 接口配置

支持 AI 服务商：OpenAI、阿里云、DeepSeek、豆包"

# 添加远程仓库（替换为您的用户名）
git remote add origin https://github.com/YOUR_USERNAME/mumu-client.git

# 推送
git branch -M main
git push -u origin main
```

## 方法二：使用 SSH（推荐）

### 1. 生成 SSH 密钥（如果还没有）

```bash
ssh-keygen -t ed25519 -C "your_email@example.com"
```

### 2. 添加公钥到 GitHub

```bash
cat ~/.ssh/id_ed25519.pub
```

复制输出内容，然后：
- 打开 GitHub → Settings → SSH and GPG keys
- 点击 "New SSH key"
- 粘贴公钥，保存

### 3. 上传代码

```bash
cd /home/bob/projects/mumu/mumu-client
git init
git add .
git commit -m "Initial commit: Mumu client v2.0"
git remote add origin git@github.com:YOUR_USERNAME/mumu-client.git
git branch -M main
git push -u origin main
```

## 验证上传成功

访问 `https://github.com/YOUR_USERNAME/mumu-client` 查看。

## 常见问题

### 1. 提示需要输入用户名密码

使用 HTTPS 方式时，GitHub 已不支持密码验证。请使用：
- GitHub 个人访问令牌 (Personal Access Token) 代替密码
- 或改用 SSH 方式

### 2. 创建 Personal Access Token

1. GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. 点击 "Generate new token (classic)"
3. 勾选 `repo` 权限
4. 生成后复制令牌，在推送时代替密码输入

### 3. 如果仓库已存在文件

如果 GitHub 仓库初始化时创建了 README 或 LICENSE：

```bash
# 先拉取远程内容
git pull origin main --rebase

# 如果有冲突，解决后
git add .
git rebase --continue

# 再推送
git push -u origin main
```

### 4. 大文件上传

如果仓库包含大文件（如图片），GitHub 限制单个文件 100MB。建议：
- 使用 Git LFS（大文件存储）
- 或将静态资源放 CDN

## 后续更新代码

```bash
# 修改文件后
git add .
git commit -m "修复: xxx功能"
git push
```

## 需要帮助？

如果在执行过程中遇到问题，请告诉我具体错误信息，我可以帮您排查。
