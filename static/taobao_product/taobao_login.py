import asyncio
from playwright.async_api import async_playwright
import json

# 定义cookies - 添加url和path
cookies = [
    {"url": "https://www.taobao.com", "name": "_cc_", "value": "URm48syIZQ%3D%3D"},
    {"url": "https://www.taobao.com", "name": "_l_g_", "value": "Ug%3D%3D"},
    {"url": "https://www.taobao.com", "name": "_tb_token_", "value": "50ab3e3707e37"},
    {"url": "https://www.taobao.com", "name": "cookie1", "value": "AnWfwqWbt9SWLkt0%2BG7WzNO10X3X%2BQ2AN3WMiOzpLUs%3D"},
    {"url": "https://www.taobao.com", "name": "cookie2", "value": "17cf8a9b5905ed476c0a11b72eb54831"},
    {"url": "https://www.taobao.com", "name": "unb", "value": "25950555"},
    {"url": "https://www.taobao.com", "name": "_nk_", "value": "%5Cu8D70%5Cu8D70%5Cu901B%5Cu901B%5Cu770B%5Cu4E00%5Cu770B"},
    {"url": "https://www.taobao.com", "name": "sgcookie", "value": "E100KYmfU9MED2YjLT53jSuNFS1spuP%2BG7SlLLal4yDMQBwFdc6r9zEeN9ivQYSEBt0Fgm1V%2FyWA3FGtOxinwKrcGWppavB8KDYmojoSWMzzWmF26kZ7h7VAxXoxdooKfwSl"},
    {"url": "https://www.taobao.com", "name": "isg", "value": "BHV1P3rTcI2gmpQx2IA3bBzDhPgv8ikEr0hn6veap-wizpXAv0De1IWPHJp4jkG8"},
    {"url": "https://www.taobao.com", "name": "cna", "value": "yq1AIoir1y8CAbc00+soVRYS"},
    {"url": "https://www.taobao.com", "name": "uc1", "value": "pas=0&cookie14=UoYZaZBGs0P7Sg%3D%3D&cookie16=U%2BGCWk%2F74Mx5tgzv3dWpnhjPaQ%3D%3D&cookie21=V32FPkk%2FgihF%2FS5nrepr&cookie15=VFC%2FuZ9ayeYq2g%3D%3D&existShop=false"},
    {"url": "https://www.taobao.com", "name": "uc3", "value": "id2=UU27Km7lLyA%3D&lg2=URm48syIIVrSKA%3D%3D&vt3=F8dD29X3cmAsOhcn2p8%3D&nk2=tMAzqq1OqhImWwiEm2A%3D"},
    {"url": "https://www.taobao.com", "name": "uc4", "value": "id4=0%40U2%2F8nPxdsjulriKsKPfycm4K1Q%3D%3D&nk4=0%40tiYFhefCjs%2B56VJOrUnUgypI01x2A3Tx7g%3D%3D"},
    {"url": "https://www.taobao.com", "name": "sg", "value": "%E7%9C%8B52"},
    {"url": "https://www.taobao.com", "name": "dnk", "value": "%5Cu8D70%5Cu8D70%5Cu901B%5Cu901B%5Cu770B%5Cu4E00%5Cu770B"},
    {"url": "https://www.taobao.com", "name": "lgc", "value": "%5Cu8D70%5Cu8D70%5Cu901B%5Cu901B%5Cu770B%5Cu4E00%5Cu770B"},
    {"url": "https://www.taobao.com", "name": "tracknick", "value": "%5Cu8D70%5Cu8D70%5Cu901B%5Cu901B%5Cu770B%5Cu4E00%5Cu770B"},
    {"url": "https://www.taobao.com", "name": "skt", "value": "0af527a6fd8f0b97"},
    {"url": "https://www.taobao.com", "name": "csg", "value": "658adbfb"},
    {"url": "https://www.taobao.com", "name": "existShop", "value": "MTc3Mzc4MTIxNQ%3D%3D"},
    {"url": "https://www.taobao.com", "name": "cancelledSubSites", "value": "empty"},
    {"url": "https://www.taobao.com", "name": "t", "value": "16bb1ba8823d4dff466466fec5a5eca9"},
    {"url": "https://www.taobao.com", "name": "tfstk", "value": "g8tmSkGbzy21voNrx5SjP8xErP0RGis1KCEO6GCZz_55k-CxhADM9dQaXqsASGAPwnCvHiyMsQdh7CC9HQjJZp1ABI3_jFAwgC1vHxOuh3A3XohjldrGXGlK9DnphKI1bXHVIfXQhOBa3tPOu0JPXObLFTi6hKs_F8FZJUJjjxE_poWN_YjPQ9FNbsW4zYXGL5SNgNPzz95P_GRN77oPp9ra7oSwUYX1a1SNbCJrEOCP_GSw_LlfER51bwKrtWoat9_YX3Bco6JVgdp94R_4lKCubl-krZfF0_qab3XDQ4Dj7uoNTeOyzE833uQOV_AfnCiQci7M7nIy0boNbURvxOxsLPfGSnslIUG0kBAfQL7kMAU1EFvwAisnjkjMe3bpSGm7Fs8ByIWkeRakFN8JcwtxaojeRKIv7Bl8ks8hQg5YzyREzl6rB3z_5ZW5E6FGV3jntP7bwYDugS_VF9dKEY4_5ZW5E6HoEykCuT6qv"},
    {"url": "https://www.taobao.com", "name": "thw", "value": "cn"},
    {"url": "https://www.taobao.com", "name": "aui", "value": "25950555"},
]

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        
        # 添加cookies
        await context.add_cookies(cookies)
        print("已添加cookies")
        
        page = await context.new_page()
        
        # 访问卖家中心
        print("正在访问卖家中心...")
        try:
            await page.goto("https://myseller.taobao.com/home.htm", wait_until="networkidle", timeout=60000)
        except Exception as e:
            print(f"页面加载超时: {e}")
        
        # 等待页面加载
        await page.wait_for_timeout(5000)
        
        # 截图
        await page.screenshot(path="卖家中心登录后.png", full_page=True)
        print("已截图: 卖家中心登录后.png")
        
        # 获取当前URL
        print(f"当前URL: {page.url}")
        
        # 获取页面标题
        print(f"页面标题: {await page.title()}")
        
        # 保存页面内容
        content = await page.content()
        with open("页面内容.html", "w") as f:
            f.write(content)
        print("已保存页面内容")
        
        await browser.close()

asyncio.run(main())
