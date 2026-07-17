const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const target = path.join(__dirname, 'windows-dom.e2e.cjs');
const proofDir = path.resolve(__dirname, '..', 'visual-proof', process.env.GS_TEST_LABEL || 'unknown');
fs.mkdirSync(proofDir, { recursive:true });

try {
  let source = fs.readFileSync(target, 'utf8');
  const after = `async function nativeClick(page, locator, label) {
  await locator.waitFor({ state:'visible', timeout:10000 });
  const box = await locator.boundingBox();
  assert.ok(box && box.width > 2 && box.height > 2, \`${'${label}'} must have a native clickable area.\`);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  const input = await page.context().newCDPSession(page);
  const bounded = (promise, action) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(\`Native input timed out: ${'${label}'} ${'${action}'}\`)), 4000))
  ]);
  console.log(\`[native-click] ${'${label}'} at ${'${x.toFixed(1)}'},${'${y.toFixed(1)}'}\`);
  try {
    await bounded(input.send('Input.dispatchMouseEvent', { type:'mouseMoved', x, y, button:'none', buttons:0 }), 'move');
    await bounded(input.send('Input.dispatchMouseEvent', { type:'mousePressed', x, y, button:'left', buttons:1, clickCount:1 }), 'press');
    await bounded(input.send('Input.dispatchMouseEvent', { type:'mouseReleased', x, y, button:'left', buttons:0, clickCount:1 }), 'release');
  } finally {
    try { await input.detach(); } catch {}
  }
  await page.waitForTimeout(100);
}`;

  if (!source.includes('Input.dispatchMouseEvent')) {
    const pattern = /async function nativeClick\(page, locator, label\) \{[\s\S]*?await page\.waitForTimeout\(90\);\r?\n\}/;
    if (!pattern.test(source)) throw new Error('Native-click helper patch target was not found.');
    source = source.replace(pattern, after);
    fs.writeFileSync(target, source);
  }

  const check = spawnSync(process.execPath, ['--check', target], { encoding:'utf8' });
  const diagnostic = [
    'CDP native-click patch applied.',
    `syntax_status=${check.status}`,
    check.stdout || '',
    check.stderr || ''
  ].join('\n');
  fs.writeFileSync(path.join(proofDir, 'NATIVE-CLICK-PATCH.txt'), diagnostic);
  if (check.status !== 0) throw new Error(`Patched Windows test failed syntax validation: ${check.stderr || check.stdout}`);
  console.log('Applied CRLF-safe bounded CDP native-click test helper.');
} catch (error) {
  fs.writeFileSync(path.join(proofDir, 'NATIVE-CLICK-PATCH-ERROR.txt'), String(error.stack || error));
  throw error;
}
