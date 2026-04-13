# Mumu 成长记录系统

Mumu 是一个给父母自用、给家人分享的宝宝成长记录系统。  
它的主轴是「家庭相册 + 成长整理 + 轻量 AI 辅助」，不是把所有体验都做成 AI 门槛。

这份 README 只写当前真实实现，不保留已经收掉的旧链路。

## 产品原则

- 先让家里人稳定地看、存、翻、分享宝宝的成长。
- AI 只在需要理解、总结、问答、生成时介入。
- 共享 AI 额度不足时，暂停的是新的 AI 操作，不是整站可用性。
- 一个 `client_id` 对应一个宝宝，`client_id` 是唯一身份源。

## 当前页面与真实边界

| 页面 | 路由 | 当前作用 | 是否默认调用 AI |
| --- | --- | --- | --- |
| 首页 | `/` | 按日期浏览照片、视频、日志、留言、上传入口 | 否 |
| 成长卡片 | `/cards` | 浏览已有卡片、漫画卡、虚拟角色卡、对比卡等 | 否 |
| 成长画像 | `/profile` | 查看已有画像、标签、雷达图、时间线 | 否 |
| 时光相册 | `/storybook` | 实时整理已有素材，导出长图和长卷 | 否 |
| AI 调用记录 | `/usage` | 查看已经发生过的 AI 调用 | 否 |
| 家庭码验证 | `/family-access` | 公网首次访问验证家庭码 | 否 |
| 帮助中心 | `/help` | 解释边界、FAQ、分页面帮助 | 否 |

### 不调用 AI / 不消耗共享 AI 额度

- 浏览首页、卡片、已有成长画像、时光相册、精选照片和历史素材
- 浏览照片视频、切换日期、打开纯净长卷、下载长图
- 上传素材、创建或重建索引、本地文件管理
- 家庭码访问、复制分享链接、留言互动
- 手动写日志、手动改日志、保存日志（`/api/log/save`）
- 查看已经生成好的卡片、画像、相册和调用记录

### 会调用 AI / 会消耗共享 AI 额度

- AI 日记生成、润色、改写
- AI 问答（`/api/ai/ask`）
- 成长画像完整刷新 / AI 复盘
- 图片理解、视频语音识别、事件解读
- AI 自动精选照片
- 漫画卡片生成
- 虚拟角色图生成

### 额度不足时，当前真实体验

- 基础相册、浏览、上传、分享、家庭码访问、留言、手动整理仍可继续使用
- 已经生成好的卡片、画像、时光相册仍可继续查看和分享
- 新的 AI 问答、AI 出图、画像刷新、AI 日记等会暂停
- `/usage` 只记录进入模型链路的动作，不代表整站都在计费

## 当前服务结构

Mumu 当前由三个 Python 进程协作，但正式环境的控制面不是手动拉起这三个进程，而是统一交给 `mumu-all.service`。

| 服务 | 入口文件 | 端口 | 作用 |
| --- | --- | --- | --- |
| 客户端 | `client_public_final.py` | `3000` | 网页前台、本地索引、本地缓存、素材浏览 |
| 服务端 | `server_public.py` | `8000` | 业务 API、鉴权、配额、聚合、命令下发 |
| 健康 AI | `../baby_health_ai/app/main.py` | `8080` | 事件解析、成长画像、画像历史、健康问答 |

说明：

- 正式环境统一由 systemd 单元 `mumu-all.service` 管理
- `/etc/systemd/system/mumu-all.service` 当前通过 `restart_all.sh restart` 统一拉起三个进程
- 因此正式重启、状态检查、故障恢复都应以 `mumu-all.service` 为准
- 当前仓库只包含 Mumu 客户端和服务端代码
- 健康 AI 服务位于同级项目 `../baby_health_ai`
- 前台“帮助中心”已经内建到客户端路由 `/help`

## 关键目录边界

### 1. 客户端本地目录

路径：`~/Documents/CZRZ/`

用途：

- 客户端配置
- 本地照片索引
- 缩略图、缓存、生成资源
- 运行时同步出来的 `runtime_web`

约束：

- 只属于客户端
- 不能把这里当成服务端或健康 AI 的真相源

### 2. 服务端数据目录

路径：`data/`

核心内容：

- `data/index.db`
- `data/users/{client_id}.db`
- `data/model_config.json`
- `data/api_config.json`
- `data/server_config.json`

### 3. 健康 AI 数据目录

路径：`../baby_health_ai/data/`

核心内容：

- `index.db`
- `children/{client_id}.db`

## 当前真实数据模型

- `client_id` 是唯一身份 ID
- 历史上的第二套宝宝 ID 已经不应再参与新逻辑
- 健康 AI 内部仍可能沿用 `child_id` 这个字段名，但表达的仍然是 `client_id`

## 当前核心链路

### 1. 日常浏览链路

1. 客户端读取本地索引和缓存，快速返回页面。
2. 用户在首页、卡片、画像、时光相册中浏览已有内容。
3. 这条链路默认不调用 AI。

### 2. 素材上传与整理链路

1. 用户上传照片或视频。
2. 客户端维护本地索引、缩略图、缓存。
3. 用户可手动整理日志或重建索引。
4. 这条链路默认不调用 AI。

### 3. 成长画像完整刷新链路

1. 健康 AI 请求服务端触发完整刷新。
2. 服务端把 `auto_review` 命令写入客户端命令队列。
3. 客户端通过心跳主动拉取命令，不再依赖服务端直接反向访问客户端公网地址。
4. 客户端本地执行图片理解、视频语音识别、素材补读、结果上传。
5. 服务端同步到健康 AI。
6. 健康 AI 基于已同步事件生成最新画像。

这条链路会消耗共享 AI 额度。

### 4. 时光相册链路

当前实现：

- `/storybook` 使用已有素材实时整理
- `generate_missing=False`
- 浏览在线页、打开纯净长卷、下载长图都不触发新的 AI 生成

## 当前数据库重点

### `data/index.db`

重要表：

- `clients`
- `tunnels`
- `news`
- `server_config`

说明：

- `clients.token_total / token_prompt / token_completion` 是配额聚合字段

### `data/users/{client_id}.db`

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

- AI 调用明细当前统一写入 `ai_sessions`
- 旧的 `token_usage` 表不再使用

### `../baby_health_ai/data/children/{client_id}.db`

当前主要表：

- `events`
- `ai_interpretations`
- `portrait_tags`
- `display_tags`
- `profile_history`
- `feedback`
- `health_metrics`

## 当前与 AI 相关的真实说明

### AI 用量如何记账

- 写入明细：`ai_sessions`
- 配额判断：`clients.token_total`
- 前台展示：服务端聚合后返回给客户端

### 为什么有些页面打开了但不消耗额度

因为这些页面只是读取已经生成好的结果。  
查看结果不等于再次调用模型。

### 为什么画像页能看，但刷新会失败

查看画像只是读现有数据；刷新画像会重新触发图片理解、视频转写、事件解析和画像生成，所以属于 AI 链路。

## 当前已下线或已改变的行为

- 旧的“每日故事 / 四格故事补齐”链路已停用
  - `/api/story/generate` 返回 `410`
  - `/api/story/status/<date>` 会提示该能力已下线
- 时光相册已经改成实时整理，不再依赖旧故事补齐缓存
- `storybook/pdf` 已下线，当前返回 `410`
  - 统一改为“下载长图”或打开 `/storybook/print`

## 本地运行

这一节用于开发调试，不代表正式环境的运维方式。

### 初始化虚拟环境

```bash
./setup_venv.sh
```

### 单独启动（开发调试）

```bash
source venv/bin/activate
python server_public.py
python client_public_final.py
```

健康 AI 需要在同级项目中单独启动：

```bash
cd ../baby_health_ai
source .venv/bin/activate
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080
```

### 一键重启三个进程（开发调试）

```bash
./restart_all.sh restart
```

## 正式环境服务管理

正式环境当前统一使用：

- `mumu-all.service`

常用命令：

```bash
# 重启正式服务
systemctl restart mumu-all.service

# 查看状态
systemctl status mumu-all.service --no-pager

# 快速确认是否存活
systemctl is-active mumu-all.service

# 服务端健康检查
curl http://127.0.0.1:8000/czrz/health
```

说明：

- 不建议把直接运行 `python server_public.py` / `python client_public_final.py` 当成正式重启方式
- `restart_all.sh` 是 `mumu-all.service` 当前调用的底层进程管理脚本，但正式操作优先用 `systemctl`

## 备份与发布

### 代码仓库边界

- 当前日常开发主目录是：`/home/bob/projects/mumu`
- 客户端公开发布仓库是：`/home/bob/projects/mumu-client`
- 私密备份仓库打包目录是：`/home/bob/projects/mumu_bundle`
- 公开仓和私密仓是两个独立 git 仓库，默认不会互相影响
- 不要在错误目录里直接执行 `git push`

### 备份当前项目

```bash
cd /home/bob/projects/mumu
./backup.sh
```

当前脚本会打包：

- `mumu`
- `../baby_health_ai`

备份输出目录当前为：

- `/media/bob/System/LINUX_FILES/projects_backup/`

### 发布客户端新版本

正式发布入口当前使用仓库根目录脚本：

```bash
cd /home/bob/projects/mumu
./release.sh v45 "release: 版本说明"
```

补充说明：

- `release.sh` 会联动 `mumu-client` 仓库完成同步、打 tag、回拉产物等动作
- 发布时建议始终显式传入版本号，不要依赖默认 tag
- 发布完成后官网下载镜像目录为：
  - `landing_page/download/`

### 推送公开仓

公开 GitHub 发布链路当前不直接在本仓库执行普通 `git push`，而是通过根目录脚本转调客户端发布仓：

```bash
cd /home/bob/projects/mumu
./release.sh v45 "release: 版本说明"
```

说明：

- 这个脚本实际会调用 `/home/bob/projects/mumu-client/release.sh`
- 它面向的是公开客户端仓库 `mumu-client`
- 适合正式发布客户端版本、打 tag、触发公开构建

### 推送私密仓

私密备份仓单独位于：

```bash
/home/bob/projects/mumu_bundle
```

推送入口脚本：

```bash
cd /home/bob/projects/mumu_bundle
./push_private_bundle.sh
```

如果要自定义分支和提交信息：

```bash
cd /home/bob/projects/mumu_bundle
./push_private_bundle.sh main "chore: update private bundle"
```

说明：

- `push_private_bundle.sh` 会从 `/home/bob/projects/mumu/.env` 读取 `GITHUB_TOKEN`
- 有本地改动时会自动执行 `git add -A`、`git commit`、`git push`
- 私密仓当前远端是：
  - `https://github.com/jxbaoxiaodong/mumu-private-bundle-20260411`
- 私密仓用于备份 `mumu` 与 `baby_health_ai`
- 私密仓当前已排除 `.env`、数据库、日志、虚拟环境、Android 构建缓存，以及 `mumu/cloudflare.exe`

## 宣传视频二次替换

如果你想把宣传视频里的图片、配音、背景音乐手动换成自己挑的版本，当前最简单的方式是：

### 1. 替换同名素材

图片截图目录：

- `outputs/gift_video_v2/captures/`

常用文件：

- `mobile_session_viewport.png`
- `mobile_calendar_modal.png`
- `calendar_day2.png`
- `profile_mobile_full.png`
- `cards_comic_mobile_full.png`
- `storybook_mobile_full.png`

配音目录：

- `outputs/gift_video_v2/audio/`

常用文件：

- `01_open.mp3`
- `02_memory.mp3`
- `03_home.mp3`
- `04_calendar.mp3`
- `05_profile.mp3`
- `06_role.mp3`
- `07_compare.mp3`
- `08_storybook.mp3`
- `09_gift.mp3`

背景音乐文件：

- `outputs/gift_video_v2/audio/warm_bed.wav`

### 2. 只重渲染，不重新抓图

替换完素材后运行：

```bash
cd /home/bob/projects/mumu
./rerender_gift_video.sh
```

说明：

- 这个脚本只会读取：
  - `outputs/gift_video_v2/resolved_spec.json`
- 它不会重新抓取页面截图
- 因此不会覆盖你手工替换掉的同名素材

如果你还改了配音文案本身，而不只是替换音频文件，记得同步修改：

- `outputs/gift_video_v2/resolved_spec.json`

否则字幕和配音内容可能对不上。

## 现有 systemd 文件

仓库内已经提供拆分后的服务文件示例，但当前正式生效的统一入口是 `mumu-all.service`：

- `mumu-health-ai.service`
- `mumu-server.service`
- `mumu-client.service`

补充说明：

- 上面三个拆分文件更适合作为结构参考或独立部署参考
- 当前这台机器的真实生产控制面仍然是 `/etc/systemd/system/mumu-all.service`

## 常见日志文件

- `client_service.log`
- `server_service.log`
- `../baby_health_ai/health_service.log`
- `~/Documents/CZRZ/logs/client.log`

## 相关入口文件

- 客户端前台：`client_public_final.py`
- 服务端：`server_public.py`
- 帮助内容源：`help_content.py`
- 前台模板：`templates/`
- 本地静态资源：`static/`

## 文档维护约束

- 这份 README 只描述当前真实实现
- 如果功能已经下线，就直接从 README 去掉，不保留“兼容旧逻辑”的叙述
- 如果 AI 边界有变化，优先更新 `/help` 与这份 README，确保前台说明和代码行为一致
