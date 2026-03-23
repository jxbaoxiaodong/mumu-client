# 配置说明

本项目使用 JSON 配置文件管理敏感信息和可自定义的选项。

## 文件说明

### 服务端配置

**文件位置**: `data/server_config.json`

**模板文件**: `data/server_config.json.example`

| 配置项 | 说明 | 环境变量替代 |
|--------|------|-------------|
| `domain.public_domain` | 服务端公网域名 | `SERVER_DOMAIN` |
| `domain.public_url` | 服务端完整 URL | - |
| `cloudflare.zone_id` | Cloudflare Zone ID | `CLOUDFLARE_ZONE_ID` |
| `cloudflare.account_id` | Cloudflare Account ID | `CLOUDFLARE_ACCOUNT_ID` |
| `cloudflare.api_token` | Cloudflare API Token | `CLOUDFLARE_API_TOKEN` |
| `admin.default_password` | 默认管理员密码 | `ADMIN_PASSWORD` |
| `admin.site_title` | 站点标题 | - |
| `admin.contact_email` | 联系邮箱 | - |

### 客户端配置

**文件位置**: `data/client_config.json`

**模板文件**: `data/client_config.json.example`

| 配置项 | 说明 | 环境变量替代 |
|--------|------|-------------|
| `server.domain` | 服务端域名 | `SERVER_DOMAIN` |
| `server.url` | 服务端完整 URL | - |
| `local.port` | 本地服务端口 | `CLIENT_PORT` |
| `local.auto_open_browser` | 启动时自动打开浏览器 | - |
| `features.enable_cloudflare_tunnel` | 启用 Cloudflare Tunnel | - |
| `features.enable_auto_update` | 启用自动更新 | - |

## 初始化步骤

### 1. 复制配置文件模板

```bash
cd data
cp server_config.json.example server_config.json
cp client_config.json.example client_config.json
```

### 2. 编辑服务端配置

编辑 `data/server_config.json`，填入你的实际值：

```json
{
  "domain": {
    "public_domain": "your-domain.com",
    "public_url": "https://your-domain.com"
  },
  "cloudflare": {
    "zone_id": "YOUR_ZONE_ID",
    "account_id": "YOUR_ACCOUNT_ID",
    "api_token": "YOUR_API_TOKEN"
  },
  "admin": {
    "default_password": "your-secure-password",
    "site_title": "宝宝成长记录",
    "contact_email": "your-email@example.com"
  }
}
```

### 3. 编辑客户端配置（可选）

如果使用默认客户端（自动连接服务端），通常不需要修改。如需修改：

```json
{
  "server": {
    "domain": "your-domain.com",
    "url": "https://your-domain.com"
  },
  "local": {
    "port": 3000
  }
}
```

## 环境变量

所有敏感配置都可以通过环境变量设置，优先级高于配置文件：

```bash
# 服务端
export SERVER_DOMAIN="your-domain.com"
export CLOUDFLARE_ZONE_ID="your-zone-id"
export CLOUDFLARE_ACCOUNT_ID="your-account-id"
export CLOUDFLARE_API_TOKEN="your-api-token"
export ADMIN_PASSWORD="your-password"

# 客户端
export SERVER_DOMAIN="your-domain.com"
export CLIENT_PORT="3000"
```

## 安全提示

1. **切勿提交真实配置文件到 Git**
   - `data/server_config.json` 和 `data/client_config.json` 已在 `.gitignore` 中
   - 只提交 `.example` 模板文件

2. **保护好 API Token**
   - Cloudflare API Token 具有 DNS 和 Tunnel 管理权限
   - AI API Token 可能产生费用
   - 建议定期轮换

3. **修改默认密码**
   - 首次启动后请立即修改默认管理员密码
   - 使用强密码（至少 8 位，包含大小写字母和数字）

## 配置优先级

配置加载的优先级（从高到低）：

1. 环境变量
2. JSON 配置文件
3. 内置默认值

这意味着：环境变量 > 配置文件 > 默认值
