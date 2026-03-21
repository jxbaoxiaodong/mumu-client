# 宝宝成长记录系统 - 客户端

一款专为记录宝宝成长点滴设计的智能系统，支持照片管理、AI 生成成长日志、公网分享等功能。

## 功能特点

- 📸 **照片管理**：智能选择精选照片，自动去重
- 🤖 **AI 日志**：基于照片自动生成成长日志
- 🌐 **公网访问**：通过 Cloudflare Tunnel 实现公网分享
- 🎨 **主题定制**：支持自定义界面主题
- 📅 **日历视图**：按日期浏览历史记录
- 👶 **宝宝画像**：AI 生成的宝宝性格兴趣画像

## 快速开始

### 方式一：下载预编译版本

前往 [Releases](https://github.com/jxbaoxiaodong/mumu-client/releases) 页面下载对应平台的安装包：

- **Windows**: 下载 `CZRZClient-Windows.zip`
- **macOS**: 下载 `CZRZClient-macOS.zip`
- **Linux**: 下载 `CZRZClient-Linux.zip`

### 方式二：从源码运行

```bash
# 克隆仓库
git clone https://github.com/jxbaoxiaodong/mumu-client.git
cd mumu-client

# 安装依赖
pip install -r requirements.txt

# 运行
python client.py
```

## 使用说明

1. 首次运行会自动打开设置向导页面
2. 填写宝宝信息（名字、生日、性别）
3. 选择照片所在的文件夹
4. 点击"完成设置"保存配置
5. 系统会自动打开主页，开始记录美好时光

## 目录结构

```
mumu-client/
├── client.py              # 主程序入口
├── theme_generator.py     # 主题生成器
├── photo_tools.py         # 照片处理工具
├── select_best_photo.py   # AI 照片选择
├── api_auth.py            # API 认证
├── templates/             # HTML 模板
├── static/                # 静态资源
└── requirements.txt       # 依赖列表
```

## 数据存储

- **配置文件**: `~/Documents/CZRZ/config.json`
- **日志文件**: `~/Documents/CZRZ/logs/`
- **主题文件**: `~/Documents/CZRZ/themes/`

## 技术栈

- **后端**: Flask (Python)
- **前端**: Bootstrap 5 + Jinja2
- **AI**: 通义千问 API
- **公网访问**: Cloudflare Tunnel

## 许可证

MIT License

---

**服务端仓库**: 联系作者获取