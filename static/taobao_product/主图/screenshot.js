const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({
    viewport: { width: 850, height: 900 }
  });
  
  // 加载主图HTML
  await page.goto(`file://${process.cwd()}/主图生成器.html`);
  await page.waitForLoadState('networkidle');
  
  // 截取每个主图
  const images = await page.$$('.main-image');
  for (let i = 0; i < images.length; i++) {
    await images[i].screenshot({ path: `主图${i+1}.png` });
    console.log(`已保存: 主图${i+1}.png`);
  }
  
  await browser.close();
})();
