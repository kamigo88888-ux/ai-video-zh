// End-to-end UI test: open editor, edit translations, render, download.
// usage: node tests/e2e_browser.js <output_dir>
const { chromium } = require('playwright');
const OUT = process.argv[2] || '.';
(async () => {
  const b = await chromium.launch();
  const p = await b.newPage({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true });
  p.on('pageerror', e => console.log('PAGEERROR', e.message));
  p.on('console', m => { if (m.type() === 'error') console.log('CONSOLE', m.text()); });
  await p.goto('http://localhost:8000/');
  await p.waitForSelector('#jobRows tr');
  await p.screenshot({ path: OUT + '/ui_list.png' });
  await p.click('#jobRows tr button:has-text("預覽")');
  await p.waitForSelector('.cap textarea');
  const ZH = { 'This gadget': '這小物根本改變人生', 'No more tangled': '從此跟打結的線\n說掰掰！',
               'Only $19.99': '今天只要 $19.99', 'Link in bio': '連結在主頁，手刀搶起來' };
  for (const cap of await p.$$('#capList .cap')) {
    const en = await (await cap.$('.en')).innerText();
    const k = Object.keys(ZH).find(k => en.startsWith(k));
    if (k) await (await cap.$('textarea')).fill(ZH[k]);
  }
  await p.waitForTimeout(1500);
  await p.evaluate(() => { document.querySelector('#vid').currentTime = 6.5; });
  await p.waitForTimeout(1200);
  await p.check('#showBoxes');
  await p.waitForTimeout(300);
  await p.screenshot({ path: OUT + '/ui_editor.png' });
  await p.click('#btnRender');
  await p.waitForFunction(() => document.querySelector('#edStatus').innerText.includes('輸出中'), null, { timeout: 30000 });
  await p.waitForFunction(() => document.querySelector('#edStatus').innerText.includes('完成'), null, { timeout: 300000 });
  const [dl] = await Promise.all([p.waitForEvent('download'), p.click('#btnDownload')]);
  await dl.saveAs(OUT + '/downloaded.mp4');
  console.log('downloaded', dl.suggestedFilename());
  await b.close();
})();
