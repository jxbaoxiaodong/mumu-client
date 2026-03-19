# Mumu 客户端配置指南

## 配置文件位置

客户端会在以下位置查找配置文件：

- **Windows**: `%USERPROFILE%\Documents\CZRZ\`
- **macOS/Linux**: `~/Documents/CZRZ/`

## 文件说明

### config.json
主配置文件，由客户端自动生成和管理。包含：
- 服务端地址
- 宝宝信息
- 媒体文件夹路径
- 本地设置

**注意**：此文件由程序自动维护，一般不需要手动编辑。

### ai_config.json（可选）
AI 配置文件，如需直接调用 AI API 而不通过服务端转发，需要手动创建此文件。

## AI 配置示例

### OpenAI 配置
```json
{
  "enabled": true,
  "ai_provider": "openai",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "api_url": "https://api.openai.com/v1",
  "text_model": "gpt-4o",
  "vision_model": "gpt-4o",
  "speech_model": "whisper-1"
}
```

### 阿里云 DashScope 配置
```json
{
  "enabled": true,
  "ai_provider": "dashscope",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "api_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
  "text_model": "qwen-vl-plus",
  "vision_model": "qwen-vl-plus",
  "speech_model": "paraformer-realtime-v2"
}
```

### DeepSeek 配置
```json
{
  "enabled": true,
  "ai_provider": "deepseek",
  "api_key": "sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "api_url": "https://api.deepseek.com/v1",
  "text_model": "deepseek-chat"
}
```

### 字节豆包配置
```json
{
  "enabled": true,
  "ai_provider": "doubao",
  "api_key": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "api_url": "https://ark.cn-beijing.volces.com/api/v3",
  "text_model": "doubao-pro-32k",
  "vision_model": "doubao-vision"
}
```

## 配置字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `enabled` | 是 | 是否启用直接 AI 调用 |
| `ai_provider` | 是 | AI 服务商: openai, dashscope, deepseek, doubao |
| `api_key` | 是 | 您的 API 密钥 |
| `api_url` | 是 | API 基础 URL |
| `text_model` | 是 | 文本生成模型名称 |
| `vision_model` | 否 | 视觉理解模型名称（用于照片分析）|
| `speech_model` | 否 | 语音识别模型名称 |

## 获取 API Key

### OpenAI
1. 访问 https://platform.openai.com/
2. 注册/登录账号
3. 进入 API Keys 页面创建新密钥

### 阿里云 DashScope
1. 访问 https://dashscope.aliyun.com/
2. 开通 DashScope 服务
3. 创建 API-KEY

### DeepSeek
1. 访问 https://platform.deepseek.com/
2. 注册/登录账号
3. 创建 API Key

### 字节豆包
1. 访问 https://console.volcengine.com/ark
2. 开通方舟服务
3. 创建推理接入点获取 API Key

## 安全提示

1. **不要提交 API Key 到 GitHub**：ai_config.json 已被添加到 .gitignore
2. **保护好您的配置文件**：API Key 相当于您的账号密码
3. **定期轮换密钥**：建议定期在服务商控制台重新生成 API Key
4. **监控使用情况**：注意查看服务商的用量和费用

## 故障排查

### AI 功能不工作
1. 检查 ai_config.json 是否存在且格式正确
2. 验证 API Key 是否有效（可在服务商控制台测试）
3. 查看客户端日志了解具体错误

### 额度不足
- 如果使用服务端转发，联系服务端管理员
- 如果直接调用，检查您的服务商账户余额

## 更多帮助

详见 [README.md](README.md) 或提交 GitHub Issue。
