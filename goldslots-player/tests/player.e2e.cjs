const assert = require('node:assert/strict');
const path = require('node:path');
const { _electron: electron } = require('playwright-core');

const root = path.resolve(__dirname, '..');
const installedExe = process.env.GS_PLAYER_EXE || '';
const executablePath = installedExe || require('electron');
const args = installedExe ? [] : ['.'];
const cwd = installedExe ? path.dirname(installedExe) : root;

async function waitForStable(page) {
  await page.waitForTimeout(120);
  await page.waitForFunction(() => document.readyState === 'complete');
}

async function setWindowSize(electronApp, width, height) {
  await electronApp.evaluate(({ BrowserWindow }, size) => {
    const win = BrowserWindow.getAllWindows()[0];
    win.unmaximize();
    win.setSize(size.width, size.height, false);
    win.center();
  }, { width, height });
}

async function assertCardsFit(page, expectedCount = 11) {
  const cards = page.locator('.game-card');
  assert.equal(await cards.count(), expectedCount, 'all eleven public games must be visible');
  const viewport = await page.evaluate(() => ({ width: innerWidth, height: innerHeight }));
  const boxes = [];
  for (let index = 0; index < expectedCount; index += 1) {
    const box = await cards.nth(index).boundingBox();
    assert.ok(box, `game card ${index + 1} must have a visible box`);
    assert.ok(box.width > 40 && box.height > 35, `game card ${index + 1} must not collapse`);
    assert.ok(box.x >= -1 && box.y >= -1, `game card ${index + 1} must not begin outside the screen`);
    assert.ok(box.x + box.width <= viewport.width + 1, `game card ${index + 1} must fit screen width`);
    assert.ok(box.y + box.height <= viewport.height + 1, `game card ${index + 1} must fit screen height`);
    boxes.push(box);
  }
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a = boxes[i], b = boxes[j];
      const overlapX = Math.max(0, Math.min(a.x + a.width, b.x + b.width) - Math.max(a.x, b.x));
      const overlapY = Math.max(0, Math.min(a.y + a.height, b.y + b.height) - Math.max(a.y, b.y));
      assert.ok(overlapX * overlapY < 2, `game cards ${i + 1} and ${j + 1} must not overlap`);
    }
  }
}

(async () => {
  const electronApp = await electron.launch({
    executablePath,
    args,
    cwd,
    env: { ...process.env, GS_PLAYER_UI_TEST: '1', ELECTRON_DISABLE_SECURITY_WARNINGS: 'true' },
    timeout: 45000
  });
  try {
    const page = await electronApp.firstWindow();
    page.on('console', (message) => console.log(`[renderer:${message.type()}] ${message.text()}`));
    page.on('pageerror', (error) => { throw error; });
    await page.waitForSelector('.game-grid', { timeout: 30000 });
    await waitForStable(page);

    for (const size of [[1024,576],[1366,768],[1920,1080],[800,1000]]) {
      await setWindowSize(electronApp, size[0], size[1]);
      await page.waitForTimeout(180);
      await assertCardsFit(page);
    }

    await setWindowSize(electronApp, 1280, 720);
    await page.waitForTimeout(150);

    // Hi-Lo: no automatic stake or decision.
    await page.locator('[data-action="game"][data-key="hi-lo-cards"]').click();
    await page.waitForSelector('[data-game="hi-lo-cards"]');
    assert.equal(await page.locator('.play-button').isDisabled(), true, 'Hi-Lo play must start disabled');
    await page.locator('[data-action="stake"][data-value="100"]').click();
    assert.equal(await page.locator('.play-button').isDisabled(), true, 'stake alone must not start Hi-Lo');
    await page.locator('[data-action="choice"][data-choice-key="hiLoPick"][data-value="higher"]').click();
    assert.equal(await page.locator('.play-button').isEnabled(), true, 'manual Higher choice must enable play');
    await page.locator('.play-button').click();
    await page.waitForSelector('.result');
    assert.equal(await page.locator('[data-action="stake"].active').count(), 0, 'stake must reset after the round');
    assert.equal(await page.locator('[data-action="choice"].active').count(), 0, 'Hi-Lo choice must reset after the round');

    // Sound and lobby controls.
    await page.locator('[data-action="back"]').click();
    await page.waitForSelector('.game-grid');
    await page.locator('[data-action="sound-open"]').click();
    await page.waitForSelector('.modal-card');
    await page.locator('[data-action="sound-toggle"][data-key="effects"]').click();
    await page.locator('[data-action="sound-close"]').click();
    assert.equal(await page.locator('.modal-card').count(), 0, 'sound panel must close');

    // Roulette requires explicit stake and layout choice.
    await page.locator('[data-action="game"][data-key="roulette"]').click();
    await page.locator('[data-action="stake"][data-value="500"]').click();
    assert.equal(await page.locator('.play-button').isDisabled(), true, 'Roulette requires a manual bet selection');
    await page.locator('[data-action="roulette"][data-type="color"][data-value="red"]').click();
    assert.equal(await page.locator('.play-button').isEnabled(), true, 'Roulette choice must enable play');
    await page.locator('.play-button').click();
    await page.waitForSelector('.result');
    await page.locator('[data-action="back"]').click();

    // Blackjack: Deal, then manual Hit and Stand.
    await page.locator('[data-action="game"][data-key="blackjack-21"]').click();
    await page.locator('[data-action="stake"][data-value="100"]').click();
    await page.locator('.play-button').click();
    await page.waitForSelector('[data-action="blackjack-hit"]');
    assert.equal(await page.locator('[data-action="blackjack-stand"]').count(), 1, 'Stand must be visible after Deal');
    await page.locator('[data-action="blackjack-hit"]').click();
    await page.waitForTimeout(120);
    assert.equal(await page.locator('[data-action="blackjack-hit"]').count(), 1, 'Hit must not automatically stand');
    await page.locator('[data-action="blackjack-stand"]').click();
    await page.waitForSelector('.result');
    await page.locator('[data-action="back"]').click();

    // Jacks or Better: Deal, player Hold, then Draw.
    await page.locator('[data-action="game"][data-key="jacks-or-better"]').click();
    await page.locator('[data-action="stake"][data-value="100"]').click();
    await page.locator('.play-button').click();
    await page.waitForSelector('[data-action="poker-hold"]');
    const firstCard = page.locator('[data-action="poker-hold"]').first();
    await firstCard.click();
    assert.ok((await firstCard.getAttribute('class')).includes('held'), 'selected Poker card must show Held');
    await page.locator('[data-action="poker-draw"]').click();
    await page.waitForSelector('.result');
    await page.locator('[data-action="back"]').click();

    // Finish must release the test session without locking the computer.
    await page.locator('[data-action="finish"]').click();
    await page.waitForSelector('.released-card');
    assert.equal(await page.locator('[data-action="next-player"]').count(), 1, 'next-player control must be clickable');

    console.log(`PASS: ${installedExe ? 'installed' : 'source'} Player click, manual-decision, responsive-layout and session tests.`);
  } finally {
    await electronApp.close();
  }
})().catch((error) => { console.error(error); process.exit(1); });
