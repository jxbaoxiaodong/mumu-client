# Mumu 客户端目录结构

```
mumu-client/
├── client.py                 # 主程序 (6945行)
│                             # - 保留默认服务端: xiaohexia.ftir.fun
│                             # - 支持HMAC签名验证
│                             # - Flask Web应用
│
├── photo_manager.py          # 照片管理模块
├── select_best_photo.py      # AI照片选择与分析
├── video_audio_processor.py  # 视频语音处理
├── theme_generator.py        # 主题生成器
├── photo_tools.py            # 照片工具集
├── auth_utils.py             # HMAC签名认证工具
├── calendar_utils.py         # 农历/节日计算
│
├── requirements.txt          # Python依赖列表
├── README.md                 # 项目说明文档
├── CONFIG.md                 # 配置指南
├── LICENSE                   # MIT许可证
├── .gitignore                # Git忽略规则
├── ai_config.example.json    # AI配置示例
│
├── templates/                # HTML模板
│   ├── base.html             # 基础模板
│   ├── index.html            # 主页面
│   ├── setup.html            # 初始设置
│   ├── profile.html          # 成长画像
│   ├── usage.html            # 使用明细
│   ├── 404.html              # 错误页面
│   ├── 500.html              # 错误页面
│   └── modals/               # 模态框组件
│       ├── ai.html
│       ├── calendar.html
│       ├── message.html
│       ├── photo_detail.html
│       ├── photo_tags.html
│       ├── settings.html
│       ├── theme_customizer.html
│       └── upload.html
│
└── static/                   # 静态资源
    ├── css/                  # 样式表
    ├── js/                   # JavaScript
    ├── images/               # 图片资源
    ├── fonts/                # 字体文件
    └── vendor/               # 第三方库
        ├── bootstrap/
        ├── fontawesome/
        ├── jquery/
        ├── datatables/
        ├── flatpickr/
        ├── masonry/
        └── social-share/
```

## 关键特性

### 安全性
- ✅ 无硬编码API Key或密码
- ✅ HMAC-SHA256签名验证
- ✅ .gitignore保护敏感文件
- ✅ xiaohexia.ftir.fun作为默认服务端保留

### 自托管AI支持
- 支持OpenAI、阿里云、DeepSeek、豆包
- 配置文件示例提供
- 详细配置文档(CONFIG.md)

### 文件统计
- Python模块: 8个
- HTML模板: 15个
- 静态文件: 35个
- 总代码行数: ~7500行
