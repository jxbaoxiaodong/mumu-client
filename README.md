# Mumu 成长记录系统

## 概述

Mumu 是一个宝宝成长记录系统，包含三大服务：

| 服务 | 文件 | 端口 | 说明 |
|------|------|------|------|
| 客户端 | `client_public_final.py` | 3000 | 用户界面，运行在用户本地机器 |
| 服务端 | `server_public.py` | 8000 | 后端API，运行在服务器 |
| 健康AI | `baby_health_ai/app/main.py` | 8080 | AI画像服务，运行在服务器 |

---

## 架构

略

### 三大原则

1. **API Token 不离开服务端** - 所有 AI 调用通过服务端代理 (`/czrz/ai/proxy/*`)
2. **客户端不直接连接健康AI** - 所有通信通过服务端代理 (`/api/ai/*`)
3. **服务端自动同步数据到健康AI** - 每15分钟定时同步，无需用户干预

---

## 存储架构

### 服务端存储

```
mumu/data/
├── index.db                    # 全局索引数据库
│   ├── clients                 # 用户列表
│   ├── tunnels                 # Tunnel池
│   ├── news                    # 新闻池
│   └── server_config           # 服务端配置
│
├── users/                      # 用户数据目录
│   ├── {client_id_1}.db        # 用户1的所有数据
│   │   ├── logs                # 日志
│   │   ├── messages            # 留言
│   │   ├── ai_sessions         # AI会话记录
│   │   ├── token_usage         # Token使用记录
│   │   ├── featured_photos     # 精选照片
│   │   └── photo_descriptions  # 照片描述
│   └── {client_id_2}.db
│
├── api_config.json             # AI API配置
├── server_config.json          # 服务端配置
├── themes/                     # 主题文件
└── client_tunnels/             # 客户端Tunnel凭证
```

### 客户端存储

```
~/Documents/CZRZ/
├── config.json                 # 客户端配置
├── baby_logs/                  # 本地日志缓存
├── photo_index/                # 照片索引
├── avatars/                    # 头像
├── thumbnails/                 # 缩略图
└── compressed/                 # 压缩版媒体
```

### 数据库表结构

#### 全局索引数据库 (index.db)

**clients 表 - 客户端信息**
| 字段 | 类型 | 说明 |
|------|------|------|
| client_id | TEXT | 客户端唯一ID（主键） |
| baby_name | TEXT | 宝宝姓名 |
| baby_gender | TEXT | 性别 |
| baby_birthday | TEXT | 出生日期 |
| user_city | TEXT | 城市 |
| ip | TEXT | IP地址 |
| subdomain | TEXT | 子域名 |
| public_url | TEXT | 公网地址 |
| status | TEXT | 状态 (online/offline/disabled) |
| is_paid | BOOLEAN | 是否付费用户 |
| token_total | INTEGER | Token总使用量 |
| registered_at | DATETIME | 注册时间 |
| last_active | DATETIME | 最后活跃时间 |

**tunnels 表 - Tunnel池**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| tunnel_id | TEXT | Cloudflare Tunnel ID |
| tunnel_secret | TEXT | Tunnel密钥 |
| subdomain | TEXT | 子域名 |
| status | TEXT | 状态 (available/allocated) |
| client_id | TEXT | 分配的客户端ID |

**news 表 - 新闻池**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| title | TEXT | 新闻标题 |
| source | TEXT | 来源 |
| date | TEXT | 日期 |
| created_at | DATETIME | 创建时间 |

#### 用户数据库 (users/{client_id}.db)

**logs 表 - 日志**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| date | TEXT | 日期 |
| content | TEXT | 日志内容 |
| weather_city | TEXT | 天气-城市 |
| weather_temperature | REAL | 天气-温度 |
| weather_condition | TEXT | 天气-状况 |
| weekday | TEXT | 星期 |
| lunar | TEXT | 农历 |
| news | TEXT | 新闻 |
| is_ai_generated | BOOLEAN | 是否AI生成 |
| generated_at | DATETIME | 生成时间 |

**messages 表 - 留言**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| date | TEXT | 日期 |
| content | TEXT | 留言内容 |
| author | TEXT | 作者 |
| type | TEXT | 类型 (text/audio) |
| created_at | DATETIME | 创建时间 |

**ai_sessions 表 - AI会话记录**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| session_id | TEXT | 会话ID |
| operation | TEXT | 操作类型 |
| prompt | TEXT | 输入提示 |
| response | TEXT | 输出内容 |
| prompt_tokens | INTEGER | 输入Token数 |
| completion_tokens | INTEGER | 输出Token数 |
| total_tokens | INTEGER | 总Token数 |
| model | TEXT | 模型名称 |
| success | BOOLEAN | 是否成功 |
| context | JSON | 上下文元数据（日期、照片名等） |
| created_at | DATETIME | 创建时间 |

**token_usage 表 - Token使用记录**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| date | TEXT | 日期 |
| operation | TEXT | 操作类型 |
| prompt_tokens | INTEGER | 输入Token数 |
| completion_tokens | INTEGER | 输出Token数 |
| total_tokens | INTEGER | 总Token数 |
| created_at | DATETIME | 创建时间 |

**speech_records 表 - 语音记录**
| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER | 主键 |
| date | TEXT | 日期 |
| video_path | TEXT | 视频路径 |
| transcript | TEXT | 语音转写文字 |
| duration | REAL | 音频时长（秒） |
| language_analysis | TEXT | 语言能力分析（JSON） |
| created_at | DATETIME | 创建时间 |

---

## API 端点

### 客户端管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/czrz/register` | 注册客户端 |
| POST | `/czrz/client/heartbeat` | 客户端心跳 |
| GET | `/czrz/client/token-usage` | 获取Token使用量 |
| POST | `/czrz/client/reset-token` | 重置Token使用量 |

### 日志

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/czrz/baby/log` | 获取日志 |
| POST | `/czrz/log/save` | 保存日志 |
| POST | `/czrz/ai/generate-log` | AI生成日志 |
| GET | `/czrz/ai/log-status` | 检查日志状态 |

**日志获取逻辑**：
```
有日志 → 返回日志 + weather + calendar + news
无日志 + 今天 → 返回实时天气（不保存）
无日志 + 非今天 → 返回空
```

**日志保存时机**：
- AI生成日志后自动保存
- 用户编辑日志后保存
- 保存时记录当天的天气、农历、新闻

### 留言

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/czrz/messages` | 提交留言 |
| GET | `/czrz/messages/{client_id}/{date}` | 获取指定日期留言 |
| GET | `/czrz/messages/all/{client_id}` | 获取所有留言 |
| DELETE | `/czrz/messages/{client_id}/{message_id}` | 删除留言 |

### 天气

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/czrz/weather` | 获取天气 |

**天气来源**：
1. Open-Meteo API（免费无需key）
2. AI联网查询（备选）

**天气存储**：仅当日志生成时保存，不单独存储

### 新闻

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/czrz/news` | 获取新闻 |
| GET | `/czrz/today-news` | 获取今日新闻 |

### 主题

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/theme` | 获取当前主题 |
| POST | `/api/theme/update` | 更新主题 |

### Tunnel

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/czrz/cloudflare/credentials` | 获取Tunnel凭证 |
| GET | `/admin/tunnel-pool` | 管理Tunnel池 |

**Tunnel分配流程**：
```
客户端心跳 → 检查是否有Tunnel → 无则自动创建 → 分配子域名 → 返回凭证
```

### 管理后台

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/admin` | 管理后台首页 |
| GET | `/admin/clients` | 设备列表 |
| GET | `/admin/clients/{client_id}/ai-status` | AI状态 |
| POST | `/admin/clients/{client_id}/reassign-tunnel` | 重新分配Tunnel |
| POST | `/admin/clients/{client_id}/reset-quota` | 重置Token配额 |
| GET | `/admin/credentials` | CF凭证设置 |
| POST | `/admin/credentials/refresh` | 刷新凭证 |

**移动端适配**：
- 侧边栏在移动端自动隐藏
- 点击汉堡菜单按钮滑出侧边栏
- 支持触摸操作

### 健康AI同步

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/czrz/ai/sync-status` | 获取同步状态 |
| POST | `/czrz/ai/sync-trigger` | 手动触发同步 |
| ANY | `/api/ai/*` | 代理健康AI请求 |

**同步机制**：
- 服务端每15分钟自动扫描未同步数据
- 查询健康AI已有事件，智能去重
- 单一线程处理，避免并发问题

### 语音记录

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/czrz/speech/record` | 保存语音记录 |
| GET | `/czrz/speech/records` | 获取语音记录列表 |

**语音处理流程**：
```
视频 → 提取音频 → 去除静音 → 限制60秒 → 语音转文字 → LLM分析语言能力
```

---

## 数据交互流程

### 客户端注册

```
1. 客户端启动
2. 读取本地配置 → 无则显示设置页面
3. 发送心跳到服务端
4. 服务端检查客户端是否存在
   - 存在 → 更新活跃时间
   - 不存在 → 自动注册
5. 服务端分配Tunnel（如果没有）
6. 返回客户端信息和凭证
```

### 日志生成

```
1. 用户点击"生成日志" 或 每天18:00自动触发
2. 客户端收集当日媒体
   - 照片：最多10张，AI分析内容
   - 视频：最多5个，提取语音(≤60秒)，语音识别
3. 上传分析结果到服务端
4. 服务端获取当日天气、农历
5. 服务端调用 LLM 生成日志
6. 保存日志到数据库（含天气、农历、新闻）
7. 同步到健康AI
```

**自动化流程**：
- 每天 18:00 客户端自动处理素材并上传
- 每天 19:00 服务端自动生成日志（有素材才生成）
- 无素材时跳过日志生成

### 日志查看

```
1. 用户切换日期
2. 客户端请求服务端 API
3. 服务端查询数据库
   - 有日志 → 返回完整数据
   - 无日志 + 今天 → 返回实时天气
   - 无日志 + 非今天 → 返回空
4. 客户端显示：
   - 有日志：天气 + 农历 + 日志 + 新闻
   - 无日志 + 今天：实时天气 + 农历 + 功能按钮
   - 无日志 + 非今天：只有日期 + 功能按钮
```

### Token 统计

```
客户端AI调用 → 服务端 → ai_sessions 表
健康AI LLM调用 → 服务端 /czrz/client/token-record → ai_sessions 表
                                              ↓
                              使用明细页面统一显示
```

**Token来源**：
- 客户端：日志生成、照片分析、语音识别等
- 健康AI：画像生成、问答、预警、事件解析等

---

## 配置文件

### 服务端配置 (data/server_config.json)

```json
{
  "domain": {
    "public_domain": "example.com",
    "public_url": "https://example.com"
  },
  "cloudflare": {
    "zone_id": "",
    "account_id": "",
    "api_token": ""
  },
  "admin": {
    "default_password": "admin123",
    "site_title": "成长记录系统"
  },
  "features": {
    "enable_tunnel_pool": true,
    "enable_client_updates": true,
    "enable_ai_log": true
  }
}
```

### API配置 (data/api_config.json)

```json
{
  "cf_api_token": "xxx",
  "cf_account_id": "xxx",
  "text_model": {
    "api_token": "xxx",
    "api_url": "https://api.example.com/v1/chat/completions",
    "model_name": "qwen3.5-plus"
  },
  "vision_model": {
    "api_token": "xxx",
    "api_url": "https://api.example.com/v1/chat/completions",
    "model_name": "qwen3-vl-flash"
  },
  "speech_model": {
    "api_token": "xxx",
    "api_url": "https://api.openai.com/v1",
    "model_name": "whisper-1"
  }
}
```

### 客户端配置 (~/Documents/CZRZ/config.json)

```json
{
  "server_domain": "example.com",
  "server_url": "https://example.com",
  "client_id": "uuid",
  "secret_key": "32字符密钥",
  "baby_name": "宝宝",
  "baby_gender": "female",
  "baby_birthday": "2025-01-01",
  "user_city": "北京",
  "log_style": "简练",
  "media_folders": ["/path/to/photos"]
}
```

---

## 启动命令

```bash
# 启动所有服务
./restart_all.sh

# 单独启动
python3 server_public.py      # 服务端
python3 client_public_final.py # 客户端
python3 baby_health_ai/app/main.py # 健康AI
```

---

## 开发说明

### 添加新的数据表

1. 在 `models.py` 中定义模型
2. 在 `database.py` 中添加操作函数
3. 在 `server_public.py` 中调用

### 添加新的API

1. 在 `server_public.py` 中添加端点
2. 使用 `database.py` 中的函数操作数据
3. 更新此文档

---

## 移动端优化

### 导航结构

主站使用 **Bootstrap 5 响应式导航栏**，支持移动端折叠：

```
📱 移动端顶部导航栏
├─ 品牌栏（固定顶部）
│   ├─ 头像 + 宝宝名称
│   └─ 汉堡菜单按钮（移动端展开/收起）
│
└─ 折叠菜单（点击展开）
    ├─ 🏠 首页
    ├─ 📤 上传（弹出模态框）
    ├─ 📅 日历（弹出模态框）
    ├─ 🤖 成长画像
    ├─ 🏷️ 标签照片
    ├─ 🎨 主题（弹出模态框）
    ├─ ⚙️ 设置（弹出模态框）
    └─ 📜 说明（弹出模态框）
```

### 移动端特殊处理

| 功能 | 桌面端 | 移动端 |
|------|--------|--------|
| 照片上传 | `multiple` 多选 | 检测浏览器支持，不支持则显示"继续添加"按钮 |
| 导航栏 | 水平展开 | 汉堡菜单 + 垂直折叠 |
| 操作按钮 | 悬停提示 | 触摸优化 |

**文件上传兼容性**：
- vivo/OPPO等国产浏览器可能不支持 `multiple` 属性
- 自动检测 UA，显示用户提示："当前浏览器不支持多选，请分多次选择"
- 提供 "继续添加" 按钮支持分批选择

---

## 未来规划

### 微信小程序（已调研）

**架构对比**：

| 特性 | 当前网页版 | 微信小程序 |
|------|-----------|-----------|
| 部署 | 用户本地运行客户端 | 微信托管 |
| AI调用 | 服务端代理 | 服务端代理 |
| 存储 | 本地 + 服务端 | 服务端 |
| 照片访问 | 本地文件夹 | 微信相册 |
| 视频处理 | 本地提取音频 | 小程序限制多 |
| 离线使用 | ✅ 完全支持 | ❌ 需联网 |
| 自动备份 | ✅ 客户端定时 | ❌ 需手动 |

**可行性评估**：
- ❌ **不建议优先开发小程序**
- 微信对AI调用、视频处理、本地存储限制较多
- 当前网页版已经通过Cloudflare Tunnel实现公网访问
- 移动端浏览器访问体验已优化

**可能的小程序功能**（简化版）：
- 查看日志和照片
- 手动上传照片
- 查看成长画像
- 发送留言

### App开发（长期规划）

**技术选型建议**：

| 方案 | 优点 | 缺点 |
|------|------|------|
| **Flutter** | 跨平台、性能好 | 需要重写UI |
| **React Native** | 生态丰富 | 性能略逊 |
| **PWA** | 成本低、可离线 | 功能受限 |

**建议**：当前网页版已经是PWA（可添加到主屏），满足基本需求。如需原生功能（推送、离线处理），再考虑Flutter。

### 近期优化方向

1. **照片管理优化**
   - 支持照片编辑（旋转、裁剪）
   - 智能相册分类（按场景、人物）
   - 照片搜索（AI语义搜索）

2. **日志功能增强**
   - 支持语音输入日志
   - 多模板选择（日记、里程碑、对比）
   - 日志导出（PDF、长图）

3. **画像准确度提升**
   - 家长反馈闭环优化
   - 多维度标签聚类
   - 成长趋势预测

---

## 文件清单

| 文件 | 说明 |
|------|------|
| `server_public.py` | 服务端主程序 |
| `client_public_final.py` | 客户端主程序 |
| `models.py` | 数据库模型定义 |
| `database.py` | 数据库操作模块 |
| `calendar_utils.py` | 农历、节日工具 |
| `theme_generator.py` | 主题生成 |
| `select_best_photo.py` | 照片精选 |
| `photo_tools.py` | 照片工具 |
| `video_audio_processor.py` | 视频语音处理 |
| `baby_log_generator.py` | 日志生成 |
| `restart_all.sh` | 一键重启脚本 |
