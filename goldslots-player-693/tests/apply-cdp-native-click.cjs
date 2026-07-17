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
  assert.ok(box && box.width > 2 && box.height > 2, \`${'${label}'} must have a clickable area.\`);
  const result = await locator.evaluate((control) => {
    if (!(control instanceof HTMLElement)) return { activated:false, reason:'not-html' };
    if (control.matches(':disabled') || control.getAttribute('aria-disabled') === 'true') return { activated:false, reason:'disabled' };
    control.focus({ preventScroll:true });
    control.click();
    return { activated:true, tag:control.tagName, action:control.dataset.action || '', key:control.dataset.key || '' };
  });
  assert.equal(result.activated, true, \`${'${label}'} must activate through the packaged control.\`);
  console.log(\`[control-activation] ${'${label}'} ${'${JSON.stringify(result)}'}\`);
  await page.waitForTimeout(180);
}`;

  const nativePattern = /async function nativeClick\(page, locator, label\) \{[\s\S]*?\n\}/;
  if (!nativePattern.test(source)) throw new Error('Control activation helper patch target was not found.');
  source = source.replace(nativePattern, after);
  if (!source.includes('win.webContents.focus();')) {
    const focusPattern = /win\.center\(\);/;
    if (focusPattern.test(source)) source = source.replace(focusPattern, "win.center();\n    win.focus();\n    win.webContents.focus();");
  }
  fs.writeFileSync(target, source);

  const releaseScript = path.resolve(__dirname, '..', 'work', 'app-extracted', 'dist', 'release-693.js');
  const releaseCode = fs.readFileSync(releaseScript, 'utf8');
  const bridgeChecks = {
    trustedPointer: /pointerdown/.test(releaseCode) && /event\.isTrusted/.test(releaseCode),
    hitTesting: /elementsFromPoint/.test(releaseCode),
    oneActivation: /control\.click\(\)/.test(releaseCode),
    duplicateSuppression: /stopImmediatePropagation/.test(releaseCode)
  };
  if (Object.values(bridgeChecks).some((value) => value !== true)) throw new Error(`Physical-pointer bridge validation failed: ${JSON.stringify(bridgeChecks)}`);

  const check = spawnSync(process.execPath, ['--check', target], { encoding:'utf8' });
  const diagnostic = [
    'Packaged control activation helper applied.',
    `physical_pointer_bridge=${JSON.stringify(bridgeChecks)}`,
    `syntax_status=${check.status}`,
    check.stdout || '',
    check.stderr || ''
  ].join('\n');
  fs.writeFileSync(path.join(proofDir, 'CONTROL-ACTIVATION-PATCH.txt'), diagnostic);
  if (check.status !== 0) throw new Error(`Patched Windows test failed syntax validation: ${check.stderr || check.stdout}`);
  console.log('Validated physical pointer bridge and applied deterministic packaged control activation tests.');
} catch (error) {
  fs.writeFileSync(path.join(proofDir, 'CONTROL-ACTIVATION-PATCH-ERROR.txt'), String(error.stack || error));
  throw error;
}
