import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # 截取详情页（全页面）
        page = await browser.new_page(viewport={'width': 750, 'height': 1000})
        await page.goto(f'file://{os.getcwd()}/详情页.html')
        await page.wait_for_load_state('networkidle')
        await page.wait_for_timeout(1000)
        
        # 全页面截图
        await page.screenshot(path='详情页_全页.png', full_page=True)
        print('已保存: 详情页_全页.png')
        
        await browser.close()

asyncio.run(main())
