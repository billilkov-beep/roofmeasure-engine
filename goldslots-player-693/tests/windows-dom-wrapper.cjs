const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const sourcePath = path.join(__dirname, 'windows-dom.e2e.cjs');
const generatedPath = path.join(__dirname, '.windows-dom-cdp-generated.cjs');
const source = fs.readFileSync(sourcePath, 'utf8');
const replacement = `async function nativeClick(page, locator, label) {
  await locator.waitFor({ state:'visible', timeout:10000 });
  const box = await locator.boundingBox();
  assert.ok(box && box.width > 2 && box.height > 2, \`${'${label}'} must have a native clickable area.\`);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const cdpInput = await page.context().newCDPSession(page);
  const bounded = (promise, action) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(\`Native input timed out: ${'${label}'} ${'${action}'}\`)), 4000))
  ]);
  console.log(\`[native-click] ${'${label}'} at ${'${x.toFixed(1)}'},${'${y.toFixed(1)}'}\`);
  try {
    await bounded(cdpInput.send('Input.dispatchMouseEvent', { type:'mouseMoved', x, y, button:'none', buttons:0 }), 'move');
    await bounded(cdpInput.send('Input.dispatchMouseEvent', { type:'mousePressed', x, y, button:'left', buttons:1, clickCount:1 }), 'press');
    await bounded(cdpInput.send('Input.dispatchMouseEvent', { type:'mouseReleased', x, y, button:'left', buttons:0, clickCount:1 }), 'release');
  } finally {
    try { await cdpInput.detach(); } catch {}
  }
  await page.waitForTimeout(100);
}

async function layoutSnapshot`;

const pattern = /async function nativeClick\(page, locator, label\) \{[\s\S]*?\n\}\n\nasync function layoutSnapshot/;
if (!pattern.test(source)) throw new Error('Native click function patch target was not found.');
fs.writeFileSync(generatedPath, source.replace(pattern, replacement));

const result = spawnSync(process.execPath, [generatedPath], {
  cwd: path.resolve(__dirname, '..'),
  env: process.env,
  encoding: 'utf8',
  timeout: 7 * 60 * 1000,
  maxBuffer: 40 * 1024 * 1024
});
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
if (result.error) console.error(result.error.stack || result.error);
process.exit(typeof result.status === 'number' ? result.status : 1);
