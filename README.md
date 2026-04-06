# Mumu 成长记录系统

Mumu 由三个进程组成：

| 服务 | 入口文件 | 端口 | 说明 |
|------|----------|------|------|
| 客户端 | `client_public_final.py` | 3000 | 本地网页、照片索引、本地缓存 |
| 服务端 | `server_public.py` | 8000 | 业务 API、鉴权、配额、数据聚合 |
| 健康 AI | `baby_health_ai/app/main.py` | 8080 | 事件解析、成长画像、问答、画像历史 |

## 当前身份模型

系统已经收口为单一身份模型：

- 一个 `client_id` 对应一个宝宝
- `client_id` 是唯一身份源
- `health_ai` 内部接口仍然使用 `child_id` 这个字段名，但它表达的就是 `client_id`
- `ai_child_id` 已经从运行时代码移除，不再参与业务判断

## 三个存储边界

### 1. 客户端缓存

路径：`~/Documents/CZRZ/`

用途：

- 客户端配置
- 客户端照片索引
- 客户端日志、缩略图、故事缓存
- 客户端 tunnel 配置

约束：

- 只允许客户端自己读写
- 服务端和 `health_ai` 不应把这里当真相源

### 2. 服务端数据

路径：`mumu/data/`

核心内容：

- `index.db`：全局索引数据库
- `users/{client_id}.db`：每个客户端自己的业务库
- `model_config.json`：文本/视觉/语音/画像模型配置
- `api_config.json`：Cloudflare 与图片模型相关兼容配置
- `server_config.json`：服务端配置与内部 token

### 3. 健康 AI 数据

路径：`baby_health_ai/data/`

核心内容：

- `index.db`：宝宝索引
- `children/{client_id}.db`：单宝宝画像与事件数据库

## 当前真实数据流

```text
客户端(3000)
  -> 服务端(8000)
  -> 健康AI(8080)

客户端本地缓存
  仅客户端使用

服务端数据库
  作为业务真相源

健康AI数据库
  作为画像与事件真相源
```

补充：

- 客户端不直接管理模型密钥
- `health_ai` 会从 `mumu/data/model_config.json` 读取模型配置
- 服务间 token 记账通过内部 `X-Internal-Token` 上报到服务端

## 当前画像刷新链路

现在有两条不同的画像刷新路径，语义不要混淆：

### 1. `health_ai` 本地画像刷新

接口：

- `POST /api/children/{child_id}/profile/refresh`

行为：

- 只处理已经在 `health_ai` 数据库里的未解析事件
- 然后直接生成画像
- 不会回头找客户端原始素材

用途：

- 供服务端 `/czrz/profile/generate-trigger` 在“素材已同步完成”后调用

### 2. `health_ai` 全链路刷新画像

接口：

- `POST /api/children/{child_id}/profile/full-refresh`

行为：

1. `health_ai` 请求服务端 `/czrz/client/auto-review-trigger`
2. 服务端把 `auto_review` 命令写入客户端命令队列，不再反向访问客户端公网域名或私网 IP
3. 客户端通过 `/czrz/client/heartbeat` 主动拉取待执行命令
4. 客户端本地启动 `/api/ai/auto-review`
5. 客户端补读未读取图片
6. 客户端补读未读取视频，并执行“提取语音 -> 转文字”
7. 客户端把照片描述、视频转写、日志、精选照片等结果上传到服务端
8. 客户端通过 `/czrz/client/command-status` 主动回报进度与最终结果
9. 客户端触发服务端同步到 `health_ai`
10. 客户端再触发服务端 `/czrz/profile/generate-trigger`
11. 服务端调用 `health_ai /profile/refresh` 完成最终画像生成

用途：

- 供 `health_ai` 页面“刷新画像”按钮使用
- 目标是与客户端手动刷新尽量保持一致

补充：

- 当前画像模型由 `health_ai` 从 `mumu/data/model_config.json` 读取
- 当前运行中的画像模型配置为 `qwen3-max`
- 不能把 `/profile/refresh` 直接改成远程触发客户端，否则会和服务端 `/czrz/profile/generate-trigger` 形成递归
- `server -> client` 的反向调用已不再作为完整刷新链路依赖项
- 客户端心跳当前为 `30` 秒一轮，用于拉取服务端命令；服务端不应再回退到客户端私网地址

### 相关鉴权

- 客户端完整刷新链路改为客户端主动拉取命令，服务端不再依赖：
  - `POST /api/ai/auto-review`
  - `GET /api/ai/auto-review/status/{task_id}`
- 新增客户端主动回报接口：
  - `POST /czrz/client/command-status`
- `health_ai` 调服务端全链路刷新时：
  - 默认可走 `127.0.0.1` 本机免签
  - 也支持额外配置环境变量 `MUMU_INTERNAL_TOKEN`

## 当前签名处置方式

服务端当前没有彻底关闭验签，而是采用“可用优先、逐步收紧”的过渡策略。

- 正常优先路径仍然是：
  - HMAC 请求头：`X-Client-ID`、`X-Timestamp`、`X-Signature`
  - 本机服务间调用：`X-Internal-Token`
- 当前合法调用端统一带固定 `User-Agent`：
  - 客户端：`CZRZ-Client/2.0`
  - 健康 AI 后端：`Mumu-Health-AI/1.0`
  - 服务端配置里仍兼容旧前缀：`Baby-Health-AI`
- 如果请求来自上述已知调用端，且服务端能从 path/query/body 中唯一解析出有效 `client_id`，那么即使验签失败，也会：
  - 记录 `signature_soft_bypass`
  - 临时放行，不直接返回 `401`
- 仍会继续拦截：
  - 未带受信 `User-Agent` 的请求
  - 无法解析出唯一 `client_id` 的请求
  - `client_id` 对应客户端不存在的请求
- `health_ai` 页面上的关键同步/刷新动作不再由浏览器直接请求服务端，而是先走 `health_ai` 本地后端代理，再由后端统一补：
  - `User-Agent: Mumu-Health-AI/1.0`
  - 可选 `X-Internal-Token`

这只是当前线上保可用方案。后续应基于日志中的 `signature_soft_bypass` 记录，逐条修复真实签名失配原因，再逐步收紧放行范围。

## 主要数据库

### `mumu/data/index.db`

重要表：

- `clients`
- `tunnels`
- `news`
- `server_config`

说明：

- `clients.token_total / token_prompt / token_completion` 是配额聚合字段
- 历史遗留 `clients.ai_child_id` 列已不再参与业务逻辑

### `mumu/data/users/{client_id}.db`

当前主要表：

- `logs`
- `messages`
- `featured_photos`
- `photo_descriptions`
- `photo_tags`
- `speech_records`
- `profile_feedbacks`
- `daily_cards`
- `ai_sessions`

说明：

- Token 调用明细现在统一记在 `ai_sessions`
- 历史 `token_usage` 表已删除

### `baby_health_ai/data/children/{client_id}.db`

当前主要表：

- `events`
- `ai_interpretations`
- `portrait_tags`
- `display_tags`
- `profile_history`
- `feedback`
- `health_metrics`

## Token 记账

当前实现：

- 写入明细：`ai_sessions`
- 配额判断：`clients.token_total`
- 前端展示：服务端从 `ai_sessions` 聚合返回

不再使用：

- `token_usage` 表

## 关键约束

### 身份约束

- 一切以 `client_id` 为准
- 新代码不允许重新引入第二套宝宝 ID 体系

### 路径约束

- `~/Documents/CZRZ` 只属于客户端
- 服务端真相源在 `mumu/data`
- `health_ai` 真相源在 `baby_health_ai/data`

### AI 约束

- 模型密钥不下发给客户端
- 客户端所有 AI 请求都应经服务端
- `health_ai` 的 LLM 使用量必须回传服务端

## 启动方式

推荐：

```bash
cd /home/bob/projects/mumu
./restart_all.sh
```

如果在系统服务里运行：

```bash
systemctl restart mumu-all.service
systemctl status mumu-all.service
```

## 常用排查

### 查看服务状态

```bash
systemctl status mumu-all.service --no-pager
```

### 查看健康 AI 配置是否已从 `model_config.json` 生效

```bash
curl http://127.0.0.1:8080/api/config/ai
```

### 查看服务端 token 记账明细

```bash
sqlite3 /home/bob/projects/mumu/data/users/<client_id>.db \
  "select id,operation,total_tokens,created_at from ai_sessions order by id desc limit 20;"
```

### 查看客户端聚合配额

```bash
sqlite3 /home/bob/projects/mumu/data/index.db \
  "select client_id,token_total,token_prompt,token_completion from clients;"
```

## 发布与备份

发布客户端：

```bash
./release.sh vXX "feat: message"
```

执行项目备份：

```bash
./backup.sh
```

## 相关文件

- `client_public_final.py`
- `server_public.py`
- `server_card_generator.py`
- `database.py`
- `models.py`
- `select_best_photo.py`
- `baby_health_ai/app/main.py`
- `baby_health_ai/app/config.py`
- `AGENT.md`

## 当前文档目标

这份 README 只描述当前真实实现，不保留已经废弃的旧链路说明。  
如果代码继续收口，优先更新这份文档，而不是追加“兼容旧逻辑”的说明。
