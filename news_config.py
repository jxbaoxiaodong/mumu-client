"""
新闻采集配置 - 多源聚合
"""

# 新闻源配置
NEWS_SOURCES = {
    "tophub": {
        "name": "今日热榜",
        "url": "https://tophub.today/c/news",
        "region": "国内",
        "type": "html"
    },
    "baidu": {
        "name": "百度热搜", 
        "url": "https://top.baidu.com/board?tab=realtime",
        "region": "国内",
        "type": "json_api"
    },
    "daojs": {
        "name": "热门聚合",
        "url": "https://hot.dao.js.cn/api/v1/items",
        "region": "国内", 
        "type": "json_api"
    },
    "netease": {
        "name": "网易热榜",
        "url": "https://m.163.com/hot",
        "region": "国内",
        "type": "html"
    },
    "zaker": {
        "name": "ZAKER",
        "url": "https://www.myzaker.com/channel/660",
        "region": "国内",
        "type": "html"
    }
}

# AI筛选提示词
AI_FILTER_PROMPT = """请从以下新闻中，严格按照标准筛选出5-8条最优质的新闻。

【筛选标准】（按优先级排序）：
1. **标题质量**：标题必须能完整表达一件事情，读者不看正文也能知道发生了什么
   ❌ 差："重磅！","快看！","来了！"
   ✅ 好："小米发布SU7电动汽车，售价21.59万起"

2. **内容领域偏好**（优先）：
   - 科技（新产品、技术突破、AI进展）
   - 体育（赛事、运动员故事）
   - 动物（萌宠、野生动物保护、感人故事）
   - 财经（经济政策、股市、企业动态）
   - 生活（健康、教育、美食、旅行）

3. **情感基调**：
   - 温馨感人
   - 积极向上
   - 有趣有料
   - 实用有价值

4. **严格排除**：
   - ❌ 政治相关（两会、政府、领导人、政策文件）
   - ❌ 军事相关（战争、武器、军队、冲突）
   - ❌ 负能量（死亡、暴力、犯罪、灾难、谣言）
   - ❌ 明星八卦、低俗娱乐
   - ❌ 标题党、无实质内容

【输出格式】JSON：
{
  "selected": [
    {
      "index": 1,
      "score": 95,
      "category": "科技",
      "sentiment": "积极",
      "reason": "重大产品发布，信息完整，对行业有影响"
    }
  ]
}

【待筛选新闻】：
{news_list}
"""

# 分类标签映射
CATEGORY_TAGS = {
    "科技": "💻",
    "体育": "⚽", 
    "动物": "🐾",
    "财经": "💰",
    "生活": "🏠",
    "健康": "💊",
    "教育": "📚",
    "娱乐": "🎬",
    "国际": "🌍",
    "其他": "📰"
}