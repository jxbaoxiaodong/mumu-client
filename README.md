# Mumu 成长记录仪 - 客户端

![Version](https://img.shields.io/badge/version-2.0-blue)
![Python](https://img.shields.io/badge/python-3.8+-green)
![License](https://img.shields.io/badge/license-MIT-yellow)

> 🎯 **发现宝宝的独特之处** - 通过 AI 深度分析照片、视频和日常记录，生成专属成长画像，帮助家长及早发现孩子的天赋和长处，因势利导，科学教育。

## ✨ 核心功能

### 🤖 AI 画像分析（核心价值）
- **特质识别**：从日常点滴中发现性格、兴趣和潜能
- **发展评估**：运动、语言、社交等维度跟踪成长轨迹  
- **教育建议**：根据画像特点提供因势利导的养育指导
- **智能日志**：自动整理照片生成成长日记

### 📷 照片管理（基础功能）
- 汇集多设备照片视频，本地集中存储
- 按日期自动归类，快速回溯任意时光
- 远程分享给家人，省去微信传图麻烦
- 数据完全本地托管，隐私自主可控

## 🚀 快速开始

### 1. 克隆仓库
```bash
git clone https://github.com/jxbaoxiaodong/mumu-client.git
cd mumu-client
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 启动客户端
```bash
python client.py
```

### 4. 完成设置
首次启动会自动打开浏览器，访问 `http://localhost:3000/setup` 进行初始配置。

## ⚙️ 配置说明

### 服务端地址
客户端默认连接 `https://xiaohexia.ftir.fun`（作者提供的服务端）。您可以：

1. **使用默认服务端**：直接开始使用，但AI功能需要联系作者获取额度
2. **自行部署服务端**：参考服务端项目部署后，在设置中修改服务端地址

### AI 功能配置

#### 方式一：使用服务端转发（默认）
AI 调用通过服务端代理，客户端无需配置 API Key。需要：
- 确保服务端已配置 AI 服务
- 联系服务端管理员获取使用额度

#### 方式二：客户端直接调用 AI API（推荐技术用户）
如需在客户端直接调用自己的 AI API，绕过服务端转发，请按以下步骤配置：

**1. 创建配置文件**

在客户端数据目录创建 `ai_config.json`：

- **Windows**: `%USERPROFILE%\Documents\CZRZ\ai_config.json`
- **macOS/Linux**: `~/Documents/CZRZ/ai_config.json`

**2. 配置文件内容**

```json
{
  "ai_provider": "openai",
  "api_key": "sk-your-api-key-here",
  "api_url": "https://api.openai.com/v1",
  "text_model": "gpt-4o",
  "vision_model": "gpt-4o",
  "enabled": true
}
```

**支持的 AI 服务商：**

| 服务商 | ai_provider | 推荐模型 |
|--------|-------------|----------|
| OpenAI | `openai` | gpt-4o, gpt-4o-mini |
| 阿里云通义千问 | `dashscope` | qwen-vl-plus, qwen-plus |
| DeepSeek | `deepseek` | deepseek-chat |
| 字节豆包 | `doubao` | doubao-vision |

**3. 修改代码启用直接调用**

在 `client.py` 中搜索 `USE_DIRECT_AI = False`，改为：

```python
USE_DIRECT_AI = True  # 启用客户端直接调用 AI
```

**4. 安装对应依赖**

```bash
# 如果使用 OpenAI
pip install openai>=1.0.0

# 如果使用阿里云 DashScope
pip install dashscope>=1.0.0
```

**5. 重启客户端**

修改配置后重启客户端，AI 功能将直接调用您的 API。

### 配置示例

#### OpenAI 配置
```json
{
  "ai_provider": "openai",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
  "api_url": "https://api.openai.com/v1",
  "text_model": "gpt-4o",
  "vision_model": "gpt-4o",
  "enabled": true
}
```

#### 阿里云 DashScope 配置
```json
{
  "ai_provider": "dashscope",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
  "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "text_model": "qwen-vl-plus",
  "vision_model": "qwen-vl-plus",
  "speech_model": "paraformer-realtime-v2",
  "enabled": true
}
```

#### DeepSeek 配置
```json
{
  "ai_provider": "deepseek",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxx",
  "api_url": "https://api.deepseek.com/v1",
  "text_model": "deepseek-chat",
  "enabled": true
}
```

## 💾 数据存储位置

所有用户数据存储在系统文档目录中：

- **Windows**: `%USERPROFILE%\Documents\CZRZ\`
- **macOS**: `~/Documents/CZRZ/`
- **Linux**: `~/Documents/CZRZ/`

包含以下文件：
- `config.json`: 用户配置文件
- `ai_config.json`: AI 配置（如启用直接调用）
- `logs/`: 客户端日志文件

## 🔒 隐私与安全

- **本地存储**：所有敏感数据仅存储在本地，不会上传到任何服务器
- **API Key 安全**：如配置直接 AI 调用，API Key 仅存储在本地配置文件
- **无追踪**：不包含任何用户行为追踪代码
- **开源透明**：完整源码开放，可审计安全性

## 📁 目录结构

```
mumu-client/
├── client.py                 # 主程序文件
├── photo_manager.py          # 照片管理模块
├── select_best_photo.py      # AI 照片选择模块
├── video_audio_processor.py  # 视频语音处理模块
├── theme_generator.py        # 主题生成模块
├── photo_tools.py            # 照片工具模块
├── auth_utils.py             # 认证工具模块
├── calendar_utils.py         # 日历工具模块
├── requirements.txt          # 依赖包列表
├── templates/                # HTML 模板文件
│   ├── base.html
│   ├── index.html
│   ├── setup.html
│   ├── profile.html
│   ├── usage.html
│   └── modals/               # 模态框模板
└── static/                   # 静态资源文件
    ├── css/
    ├── js/
    ├── images/
    ├── fonts/
    └── vendor/               # 第三方库
```

## 🔧 开发说明

### 运行模式

客户端支持两种运行模式：

1. **连接模式**（默认）：连接远程服务端，使用服务端 AI 转发
2. **独立模式**：不连接服务端，仅使用本地功能 + 直接 AI 调用

### 环境变量

| 变量名 | 说明 |
|--------|------|
| `MUMU_DEBUG` | 设置为 `1` 启用调试模式 |
| `MUMU_PORT` | 指定客户端端口，默认 `3000` |
| `MUMU_DATA_DIR` | 自定义数据目录 |

## 🤝 贡献指南

欢迎提交 Issue 和 Pull Request！请遵循以下准则：

1. 提交前运行代码确保没有语法错误
2. 保持代码风格一致
3. 更新相关文档
4. 不要提交包含 API Key 或密码的代码

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源许可。

## 💬 支持

如有问题，请提交 [GitHub Issue](https://github.com/jxbaoxiaodong/mumu-client/issues) 或联系项目维护者。

---

**注意**：
- 默认服务端 `xiaohexia.ftir.fun` 仅供体验和测试，AI 功能需要联系作者获取额度
- 建议技术用户自行部署服务端或配置直接 AI 调用以获得最佳体验
