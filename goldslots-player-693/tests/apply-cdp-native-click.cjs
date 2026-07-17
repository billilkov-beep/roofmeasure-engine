const fs = require('node:fs');
const path = require('node:path');

const target = path.join(__dirname, 'windows-dom.e2e.cjs');
let source = fs.readFileSync(target, 'utf8');
const before = `async function nativeClick(page, locator, label) {
  await locator.waitFor({ state:'visible', timeout:10000 });
  const box = await locator.boundingBox();
  assert.ok(box && box.width > 2 && box.height > 2, \`${'${label}'} must have a native clickable area.\`);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(90);
}`;
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

if (source.includes(before)) {
  source = source.replace(before, after);
  fs.writeFileSync(target, source);
  console.log('Applied bounded CDP native-click test helper.');
} else if (source.includes("Input.dispatchMouseEvent")) {
  console.log('Bounded CDP native-click test helper is already applied.');
} else {
  throw new Error('Native-click helper patch target was not found.');
}
