const puppeteer = require('puppeteer');
const path = require('path');

(async () => {
  const target = 'file://' + path.resolve(__dirname, 'unoicu_test.html');
  const browser = await puppeteer.launch({
    headless: 'new',
    args: [
      '--allow-file-access-from-files',
      '--no-sandbox',
      '--disable-setuid-sandbox'
    ]
  });

  const page = await browser.newPage();
  page.on('console', msg => console.log('[chromium]', msg.text()));

  try {
    await page.goto(target, { waitUntil: 'load' });
    await page.waitForFunction(() => typeof Module !== 'undefined' && Module.unoicuResult !== undefined, { timeout: 20000 });
    const result = await page.evaluate(() => Module.unoicuResult);
    if (!result || !result.ok) {
      throw new Error(`unoicuResult indicated failure: ${JSON.stringify(result)}`);
    }
    console.log('unoicu wasm validation passed');
  } finally {
    await browser.close();
  }
})();
