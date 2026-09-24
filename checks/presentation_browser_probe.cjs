// Optional browser QA of the exact HTML fragments emitted by live_coding_queue.
// This verifies layout; it does not claim that Codex Desktop rendered the reference.
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

async function main() {
  const root = path.resolve(process.argv[2]);
  const files = fs.readdirSync(root).filter(name => name.endsWith('.html'));
  if (!files.length) throw new Error('No emitted HTML fragments to inspect');
  const browser = await chromium.launch({headless: true, channel: process.env.PLAYWRIGHT_CHANNEL || 'msedge'});
  const results = [];
  try {
    const page = await browser.newPage();
    for (const scheme of ['light', 'dark']) {
      for (const width of [360, 900]) {
        await page.setViewportSize({width, height: 800});
        for (const file of files) {
          const colors = scheme === 'light' ? ['#fff', '#fff', '#181818'] : ['#171717', '#242424', '#eee'];
          const css = `body{margin:12px;background:${colors[0]};color:${colors[2]};--card:${colors[1]};--card-foreground:${colors[2]};--foreground:${colors[2]};--border:#888}`;
          await page.setContent('<style>' + css + '</style>' + fs.readFileSync(path.join(root, file), 'utf8'));
          const data = await page.evaluate(() => ({
            text: document.querySelector('section')?.innerText,
            overflow: document.documentElement.scrollWidth > innerWidth,
            sections: document.querySelectorAll('section').length,
          }));
          if (data.overflow || !data.text || data.sections !== 1) {
            throw new Error(`${file} at ${width}/${scheme}: ${JSON.stringify(data)}`);
          }
          results.push({file, scheme, width, overflow: false});
          if (file.startsWith('frontend-') && data.text.includes('Antigravity') && data.text.includes('GitHub Copilot')) {
            await page.screenshot({path: path.join(root, `home-${scheme}-${width}.png`)});
          }
        }
      }
    }
    fs.writeFileSync(path.join(root, 'browser-layout.json'), JSON.stringify(results, null, 2));
    console.log(JSON.stringify({checked: results.length, files: files.length, overflowFailures: 0}));
  } finally {
    await browser.close();
  }
}
main().catch(error => { console.error(error); process.exitCode = 1; });
