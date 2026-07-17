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
  const input = page.__gsCdp;
  assert.ok(input && typeof input.send === 'function', 'The active Electron CDP input session must be available.');
  const bounded = (promise, action) => Promise.race([
    promise,
    new Promise((_, reject) => setTimeout(() => reject(new Error(\`Native input timed out: ${'${label}'} ${'${action}'}\`)), 4000))
  ]);
  console.log(\`[native-click] ${'${label}'} at ${'${x.toFixed(1)}'},${'${y.toFixed(1)}'}\`);
  await bounded(input.send('Input.dispatchMouseEvent', { type:'mouseMoved', x, y, button:'none', buttons:0 }), 'move');
  input.send('Input.dispatchMouseEvent', { type:'mousePressed', x, y, button:'left', buttons:1, clickCount:1 }).catch(() => {});
  await page.waitForTimeout(60);
  input.send('Input.dispatchMouseEvent', { type:'mouseReleased', x, y, button:'left', buttons:0, clickCount:1 }).catch(() => {});
  await page.waitForTimeout(240);
}`;

  if (!source.includes('Input.dispatchMouseEvent')) {
    const pattern = /async function nativeClick\(page, locator, label\) \{[\s\S]*?await page\.waitForTimeout\(90\);\r?\n\}/;
    if (!pattern.test(source)) throw new Error('Native-click helper patch target was not found.');
    source = source.replace(pattern, after);
  }
  if (!source.includes('page.__gsCdp = cdp;')) {
    const cdpPattern = /const cdp = await page\.context\(\)\.newCDPSession\(page\);/;
    if (!cdpPattern.test(source)) throw new Error('Active CDP session assignment target was not found.');
    source = source.replace(cdpPattern, "const cdp = await page.context().newCDPSession(page);\n    page.__gsCdp = cdp;");
  }
  if (!source.includes('win.webContents.focus();')) {
    const focusPattern = /win\.center\(\);/;
    if (!focusPattern.test(source)) throw new Error('BrowserWindow focus patch target was not found.');
    source = source.replace(focusPattern, "win.center();\n    win.focus();\n    win.webContents.focus();");
  }
  fs.writeFileSync(target, source);

  const check = spawnSync(process.execPath, ['--check', target], { encoding:'utf8' });
  const diagnostic = [
    'Focused physical-pointer bridge test patch applied.',
    'Mouse press and release are sent without waiting for a rerender acknowledgement.',
    `syntax_status=${check.status}`,
    check.stdout || '',
    check.stderr || ''
  ].join('\n');
  fs.writeFileSync(path.join(proofDir, 'NATIVE-CLICK-PATCH.txt'), diagnostic);
  if (check.status !== 0) throw new Error(`Patched Windows test failed syntax validation: ${check.stderr || check.stdout}`);
  console.log('Applied focused physical-pointer bridge test helper.');
} catch (error) {
  fs.writeFileSync(path.join(proofDir, 'NATIVE-CLICK-PATCH-ERROR.txt'), String(error.stack || error));
  throw error;
}
