const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const sourcePath = path.join(__dirname, 'visual-installed.e2e.cjs');
const generatedPath = path.join(__dirname, '.visual-generated.cjs');
const proofDir = path.resolve(__dirname, '..', 'visual-proof', process.env.GS_TEST_LABEL || 'installed');
fs.mkdirSync(proofDir, { recursive: true });

let source = fs.readFileSync(sourcePath, 'utf8');
source = source.replace(
  '    const page = await app.firstWindow();',
  "    const page = await app.firstWindow();\n    page.setDefaultTimeout(7000);\n    page.setDefaultNavigationTimeout(7000);"
);
source = source.replace(
  "    await page.waitForSelector('.game-grid', { timeout: 30000 });\n\n    const sizes",
  `    await page.waitForSelector('.game-grid', { state: 'attached', timeout: 30000 });
    const initialLayout = await page.evaluate(() => {
      const pick = (selector) => {
        const node = document.querySelector(selector);
        if (!node) return null;
        const rect = node.getBoundingClientRect();
        const style = getComputedStyle(node);
        return { selector, rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height}, display:style.display, visibility:style.visibility, opacity:style.opacity, overflow:style.overflow, gridRows:style.gridTemplateRows, gridColumns:style.gridTemplateColumns };
      };
      return { readyState:document.readyState, visibilityState:document.visibilityState, viewport:{width:innerWidth,height:innerHeight}, shell:pick('.shell'), content:pick('.content'), lobby:pick('.lobby'), grid:pick('.game-grid'), firstCard:pick('.game-tile'), logo:pick('.gc-mark') };
    });
    fs.writeFileSync(path.join(SHOTS, 'INITIAL-LAYOUT.json'), JSON.stringify(initialLayout, null, 2));
    await page.screenshot({ path: path.join(SHOTS, 'raw-first-lobby.png') });

    const sizes`
);
source = source.replace(
  '    await app.close();',
  "    try {\n      await Promise.race([app.close(), new Promise((resolve) => setTimeout(resolve, 5000))]);\n    } finally {\n      const process = app.process();\n      if (process && process.exitCode == null) process.kill();\n    }"
);
fs.writeFileSync(generatedPath, source);

const result = spawnSync(process.execPath, [generatedPath], {
  cwd: path.resolve(__dirname, '..'),
  env: process.env,
  encoding: 'utf8',
  timeout: 8 * 60 * 1000,
  maxBuffer: 20 * 1024 * 1024
});
const output = [result.stdout || '', result.stderr || '', result.error ? String(result.error.stack || result.error) : ''].join('\n');
fs.writeFileSync(path.join(proofDir, 'TEST-OUTPUT.txt'), output);
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
if (result.error) console.error(result.error);
process.exit(typeof result.status === 'number' ? result.status : 1);
