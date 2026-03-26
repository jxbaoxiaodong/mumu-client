import asyncio
from playwright.async_api import async_playwright
import os

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        
        # 截取主图
        page = await browser.new_page(viewport={'width': 850, 'height': 900})
        await page.goto(f'file://{os.getcwd()}/主图生成器.html')
        await page.wait_for_load_state('networkidle')
        await page.wait_for_timeout(1000)
        
        images = await page.query_selector_all('.main-image')
        for i, img in enumerate(images):
            await img.screenshot(path=f'主图{i+1}.png')
            print(f'已保存: 主图{i+1}.png')
        
        await browser.close()

asyncio.run(main())
