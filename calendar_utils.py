"""
日历工具模块 - 农历、节日等
"""

from datetime import datetime, date
from typing import Optional, Dict
from zhdate import ZhDate


# 农历月份名称
LUNAR_MONTH_NAMES = {
    1: "正月",
    2: "二月",
    3: "三月",
    4: "四月",
    5: "五月",
    6: "六月",
    7: "七月",
    8: "八月",
    9: "九月",
    10: "十月",
    11: "冬月",
    12: "腊月",
}

# 农历日期名称
LUNAR_DAY_NAMES = {
    1: "初一",
    2: "初二",
    3: "初三",
    4: "初四",
    5: "初五",
    6: "初六",
    7: "初七",
    8: "初八",
    9: "初九",
    10: "初十",
    11: "十一",
    12: "十二",
    13: "十三",
    14: "十四",
    15: "十五",
    16: "十六",
    17: "十七",
    18: "十八",
    19: "十九",
    20: "二十",
    21: "廿一",
    22: "廿二",
    23: "廿三",
    24: "廿四",
    25: "廿五",
    26: "廿六",
    27: "廿七",
    28: "廿八",
    29: "廿九",
    30: "三十",
}

# 星期名称
WEEKDAY_NAMES = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]

# 公历节日
SOLAR_FESTIVALS = {
    "01-01": "元旦",
    "02-14": "情人节",
    "03-08": "妇女节",
    "03-12": "植树节",
    "04-01": "愚人节",
    "05-01": "劳动节",
    "05-04": "青年节",
    "06-01": "儿童节",
    "07-01": "建党节",
    "08-01": "建军节",
    "09-10": "教师节",
    "10-01": "国庆节",
    "10-31": "万圣节",
    "11-11": "双十一",
    "12-24": "平安夜",
    "12-25": "圣诞节",
}

# 农历节日（农历月份-日期: 节日名）
LUNAR_FESTIVALS = {
    "1-1": "春节",
    "1-15": "元宵节",
    "2-2": "龙抬头",
    "5-5": "端午节",
    "7-7": "七夕",
    "7-15": "中元节",
    "8-15": "中秋节",
    "9-9": "重阳节",
    "12-8": "腊八节",
    "12-23": "小年",
    "12-30": "除夕",  # 除夕是腊月最后一天
}


def get_lunar_date(target_date: date = None) -> Dict:
    """
    获取农历日期信息

    Returns:
        {
            "lunar_month": 1,           # 农历月份数字
            "lunar_day": 28,            # 农历日期数字
            "lunar_month_name": "正月", # 农历月份名称
            "lunar_day_name": "廿八",   # 农历日期名称
            "lunar_display": "正月廿八", # 农历显示字符串
            "lunar_festival": None      # 农历节日（如果有）
        }
    """
    if target_date is None:
        target_date = date.today()

    if isinstance(target_date, datetime):
        target_date = target_date.date()

    try:
        dt = datetime.combine(target_date, datetime.min.time())
        zh = ZhDate.from_datetime(dt)
        lunar_month = zh.lunar_month
        lunar_day = zh.lunar_day

        lunar_month_name = LUNAR_MONTH_NAMES.get(lunar_month, f"{lunar_month}月")
        lunar_day_name = LUNAR_DAY_NAMES.get(lunar_day, f"{lunar_day}日")

        # 检查农历节日
        lunar_festival = LUNAR_FESTIVALS.get(f"{lunar_month}-{lunar_day}")

        return {
            "lunar_month": lunar_month,
            "lunar_day": lunar_day,
            "lunar_month_name": lunar_month_name,
            "lunar_day_name": lunar_day_name,
            "lunar_display": f"{lunar_month_name}{lunar_day_name}",
            "lunar_festival": lunar_festival,
        }
    except Exception as e:
        return {
            "lunar_month": None,
            "lunar_day": None,
            "lunar_month_name": "",
            "lunar_day_name": "",
            "lunar_display": "",
            "lunar_festival": None,
            "error": str(e),
        }


def get_weekday(target_date: date = None) -> str:
    """获取星期名称"""
    if target_date is None:
        target_date = date.today()
    if isinstance(target_date, datetime):
        target_date = target_date.date()
    return WEEKDAY_NAMES[target_date.weekday()]


def get_solar_festival(target_date: date = None) -> Optional[str]:
    """获取公历节日"""
    if target_date is None:
        target_date = date.today()
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    month_day = target_date.strftime("%m-%d")
    return SOLAR_FESTIVALS.get(month_day)


def get_calendar_info(target_date: date = None) -> Dict:
    """
    获取完整日历信息

    Returns:
        {
            "date": "2026-03-16",
            "weekday": "星期一",
            "lunar_month": 1,
            "lunar_day": 28,
            "lunar_display": "正月廿八",
            "solar_festival": None,
            "lunar_festival": None,
            "festival": None  # 优先显示的节日
        }
    """
    if target_date is None:
        target_date = date.today()
    if isinstance(target_date, datetime):
        target_date = target_date.date()

    lunar_info = get_lunar_date(target_date)
    weekday = get_weekday(target_date)
    solar_festival = get_solar_festival(target_date)
    lunar_festival = lunar_info.get("lunar_festival")

    # 优先显示农历节日，其次公历节日
    festival = lunar_festival or solar_festival

    return {
        "date": target_date.strftime("%Y-%m-%d"),
        "weekday": weekday,
        "lunar_month": lunar_info.get("lunar_month"),
        "lunar_day": lunar_info.get("lunar_day"),
        "lunar_display": lunar_info.get("lunar_display"),
        "solar_festival": solar_festival,
        "lunar_festival": lunar_festival,
        "festival": festival,
    }


if __name__ == "__main__":
    info = get_calendar_info()
    print(f"日期: {info['date']}")
    print(f"星期: {info['weekday']}")
    print(f"农历: {info['lunar_display']}")
    if info["festival"]:
        print(f"节日: {info['festival']}")
