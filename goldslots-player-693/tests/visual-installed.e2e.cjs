const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');
const { _electron: electron } = require('playwright-core');

const ROOT = path.resolve(__dirname, '..');
const EXE = process.env.GS_PLAYER_EXE;
const LABEL = process.env.GS_TEST_LABEL || 'installed';
const SHOTS = path.join(ROOT, 'visual-proof', LABEL);
const GAMES = ['lucky-reels','five-card-poker','jacks-or-better','blackjack-21','keno','roulette','money-wheel','bingo','hi-lo-cards','baccarat','craps'];
if (!EXE) throw new Error('GS_PLAYER_EXE is required.');
fs.mkdirSync(SHOTS, { recursive: true });

async function setSize(app, width, height) {
  await app.evaluate(({ BrowserWindow }, value) => {
    const win = BrowserWindow.getAllWindows()[0];
    win.show();
    win.unmaximize();
    win.setFullScreen(false);
    win.setKiosk(false);
    win.setAlwaysOnTop(false);
    win.setSize(value.width, value.height, false);
    win.center();
  }, { width, height });
}

async function collectLayout(page) {
  return page.evaluate(() => {
    const pick = (selector) => {
      const node = document.querySelector(selector);
      if (!node) return null;
      const rect = node.getBoundingClientRect();
      const style = getComputedStyle(node);
      return {
        selector,
        rect:{x:rect.x,y:rect.y,width:rect.width,height:rect.height},
        display:style.display,
        visibility:style.visibility,
        opacity:style.opacity,
        overflow:style.overflow,
        position:style.position,
        gridRows:style.gridTemplateRows,
        gridColumns:style.gridTemplateColumns
      };
    };
    return {
      readyState:document.readyState,
      visibilityState:document.visibilityState,
      viewport:{width:innerWidth,height:innerHeight},
      body:pick('body'), app:pick('#app'), shell:pick('.shell'), content:pick('.content'),
      lobby:pick('.lobby'), grid:pick('.game-grid'), firstCard:pick('.game-tile'), logo:pick('.gc-mark')
    };
  });
}

async function assertScreenshotVisual(file, minimumBytes = 90000, minimumStdev = 18) {
  const stat = fs.statSync(file);
  assert.ok(stat.size >= minimumBytes, `${path.basename(file)} must contain a detailed rendered design.`);
  const stats = await sharp(file).stats();
  const average = stats.channels.slice(0, 3).reduce((sum, item) => sum + item.stdev, 0) / 3;
  assert.ok(average >= minimumStdev, `${path.basename(file)} must not be a flat or vanished interface; stdev=${average.toFixed(2)}.`);
}

async function assertLobby(page, expectedWidth, expectedHeight) {
  await page.waitForSelector('.game-grid', { state:'attached', timeout:10000 });
  const layout = await collectLayout(page);
  fs.writeFileSync(path.join(SHOTS, `layout-${expectedWidth}x${expectedHeight}.json`), JSON.stringify(layout, null, 2));
  assert.ok(layout.grid?.rect.width > 200 && layout.grid?.rect.height > 150, `Lobby grid must be visible; received ${JSON.stringify(layout.grid)}`);

  const cards = page.locator('button.game-tile');
  assert.equal(await cards.count(), 11, 'The approved eleven original visual lobby cards must render.');
  const imageData = await page.locator('button.game-tile img').evaluateAll((images) => images.map((image) => ({
    src:image.getAttribute('src'), complete:image.complete, width:image.naturalWidth, height:image.naturalHeight
  })));
  assert.equal(imageData.length, 11, 'Every original lobby card must contain an image element.');
  for (const [index, image] of imageData.entries()) {
    assert.ok(image.complete, `Lobby image ${index + 1} must finish loading.`);
    assert.ok(image.width >= 200 && image.height >= 275, `Lobby image ${index + 1} must be the real artwork, not an empty placeholder.`);
    assert.match(image.src || '', /assets\/game-menu\/.+\.webp$/i, `Lobby image ${index + 1} must use the original game-menu artwork.`);
  }

  const styles = await page.evaluate(() => [...document.styleSheets].map((sheet) => sheet.href || 'inline'));
  for (const required of ['goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.css','release-693.css']) {
    assert.ok(styles.some((value) => value.includes(required)), `Original premium stylesheet ${required} must be active.`);
  }

  const logo = page.locator('.gc-mark').first();
  const logoStyle = await logo.evaluate((node) => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return { backgroundImage:style.backgroundImage, width:rect.width, height:rect.height };
  });
  assert.ok(logoStyle.width >= 36 && logoStyle.height >= 36, 'The Gold Slots header logo must be visible.');
  assert.match(logoStyle.backgroundImage, /goldslots-logo/i, 'The original Gold Slots logo artwork must be applied.');

  const viewport = await page.evaluate(() => ({ width:innerWidth, height:innerHeight }));
  assert.ok(Math.abs(viewport.width - expectedWidth) < 50, `Window width must match ${expectedWidth}; received ${viewport.width}.`);
  assert.ok(Math.abs(viewport.height - expectedHeight) < 100, `Window height must match ${expectedHeight}; received ${viewport.height}.`);
  const boxes = [];
  for (let index = 0; index < 11; index += 1) {
    const box = await cards.nth(index).boundingBox();
    assert.ok(box && box.width > 55 && box.height > 45, `Game card ${index + 1} must remain visible and usable.`);
    assert.ok(box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 2 && box.y + box.height <= viewport.height + 2, `Game card ${index + 1} must remain inside the screen.`);
    boxes.push(box);
  }
  for (let i = 0; i < boxes.length; i += 1) {
    for (let j = i + 1; j < boxes.length; j += 1) {
      const a=boxes[i], b=boxes[j];
      const overlapX=Math.max(0,Math.min(a.x+a.width,b.x+b.width)-Math.max(a.x,b.x));
      const overlapY=Math.max(0,Math.min(a.y+a.height,b.y+b.height)-Math.max(a.y,b.y));
      assert.ok(overlapX * overlapY < 3, `Lobby cards ${i + 1} and ${j + 1} must not overlap.`);
    }
  }
}

(async () => {
  const app = await electron.launch({
    executablePath:EXE,
    args:['--disable-gpu'],
    cwd:path.dirname(EXE),
    env:{...process.env,GS_PLAYER_UI_TEST:'1',ELECTRON_DISABLE_SECURITY_WARNINGS:'true'},
    timeout:45000
  });
  try {
    const page = await app.firstWindow();
    page.setDefaultTimeout(7000);
    page.setDefaultNavigationTimeout(7000);
    const pageErrors=[];
    page.on('pageerror',(error)=>pageErrors.push(error.message));
    page.on('console',(message)=>console.log(`[renderer:${message.type()}] ${message.text()}`));
    await page.waitForSelector('.game-grid',{state:'attached',timeout:30000});
    await setSize(app,1366,768);
    await page.waitForTimeout(250);
    fs.writeFileSync(path.join(SHOTS,'INITIAL-LAYOUT.json'),JSON.stringify(await collectLayout(page),null,2));
    await page.screenshot({path:path.join(SHOTS,'raw-first-lobby.png')});

    for (const [width,height] of [[1024,576],[1366,768],[1920,1080],[800,1000]]) {
      await setSize(app,width,height);
      await page.waitForTimeout(250);
      await assertLobby(page,width,height);
      const shot=path.join(SHOTS,`lobby-${width}x${height}.png`);
      await page.screenshot({path:shot});
      await assertScreenshotVisual(shot);
    }

    await setSize(app,1366,768);
    await page.waitForTimeout(200);
    const logoBox=await page.locator('.gc-mark').first().boundingBox();
    assert.ok(logoBox,'Gold Slots logo must have a visible bounding box.');
    const logoShot=path.join(SHOTS,'gold-slots-logo.png');
    await page.screenshot({path:logoShot,clip:{x:Math.max(0,logoBox.x-3),y:Math.max(0,logoBox.y-3),width:logoBox.width+6,height:logoBox.height+6}});
    await assertScreenshotVisual(logoShot,1500,8);

    for (const key of GAMES) {
      await page.locator(`button.game-tile[data-key="${key}"]`).click();
      await page.waitForSelector('.game-shell',{state:'visible'});
      assert.equal(await page.locator('.game-stage').count(),1,`${key} must open its original game stage.`);
      const gameShot=path.join(SHOTS,`game-${key}.png`);
      await page.screenshot({path:gameShot});
      await assertScreenshotVisual(gameShot,80000,16);
      await page.locator('[data-action="lobby"]').click();
      await page.waitForSelector('.game-grid',{state:'attached'});
    }

    await page.locator('button.game-tile[data-key="hi-lo-cards"]').click();
    assert.equal(await page.locator('.play-action').isDisabled(),true,'Hi-Lo starts disabled until the player acts.');
    await page.locator('[data-action="bet"]').first().click();
    assert.equal(await page.locator('.play-action').isDisabled(),true,'Selecting only a stake must not choose Higher/Lower.');
    await page.locator('[data-action="choice"][data-key="hiLoPick"][data-value="higher"]').click();
    assert.equal(await page.locator('.play-action').isEnabled(),true,'The player Higher choice must enable Hi-Lo play.');
    await page.locator('[data-action="lobby"]').click();

    await page.evaluate(()=>{state.demoSession=true;state.demoBalanceCents=500000;Math.random=()=>0.31;render(true);});
    await page.waitForSelector('.game-grid',{state:'attached'});
    await page.locator('button.game-tile[data-key="blackjack-21"]').click();
    await page.locator('[data-action="bet"]').first().click();
    await page.locator('.play-action').click();
    await page.waitForTimeout(450);
    if(await page.locator('[data-action="blackjack-hit"]').count()){
      await page.locator('[data-action="blackjack-hit"]').click();
      await page.waitForTimeout(300);
      if(await page.locator('[data-action="blackjack-stand"]').count())await page.locator('[data-action="blackjack-stand"]').click();
    }
    await page.waitForTimeout(300);
    assert.equal(await page.locator('.round-result').count(),1,'Blackjack must complete Deal and manual Hit/Stand interaction.');
    await page.locator('[data-action="lobby"]').click();

    await page.locator('button.game-tile[data-key="jacks-or-better"]').click();
    await page.locator('[data-action="bet"]').first().click();
    await page.locator('.play-action').click();
    await page.waitForSelector('[data-action="poker-hold"]');
    await page.locator('[data-action="poker-hold"]').first().click();
    assert.ok((await page.locator('[data-action="poker-hold"]').first().getAttribute('class')).includes('held'),'Poker Hold must visibly preserve the selected card.');
    await page.locator('[data-action="poker-draw"]').click();
    await page.waitForTimeout(450);
    assert.equal(await page.locator('[data-action="poker-hold"]').count(),0,'Poker Draw must finish the manual hand.');
    await page.locator('[data-action="lobby"]').click();

    await page.locator('[data-action="sound-open"]').click();
    await page.waitForSelector('.sound-mixer');
    await page.locator('[data-action="sound-toggle"][data-key="effects"]').click();
    await page.locator('[data-action="sound-close"]').click();
    assert.equal(await page.locator('.sound-mixer').count(),0,'The original sound mixer must open, respond, and close.');

    const windowState=await app.evaluate(({BrowserWindow})=>{const win=BrowserWindow.getAllWindows()[0];return{kiosk:win.isKiosk(),fullscreen:win.isFullScreen(),alwaysOnTop:win.isAlwaysOnTop()};});
    assert.deepEqual(windowState,{kiosk:false,fullscreen:false,alwaysOnTop:false},'Player must start normally until a fresh Super Admin kiosk policy arrives.');
    assert.deepEqual(pageErrors,[],`Renderer errors are not allowed: ${pageErrors.join(' | ')}`);
    console.log(`PASS: ${LABEL} original logo, eleven lobby artworks, premium design, native clicks, responsive layouts, manual Hi-Lo, Blackjack and Poker.`);
  } finally {
    try { await Promise.race([app.close(),new Promise((resolve)=>setTimeout(resolve,5000))]); }
    finally { const child=app.process(); if(child&&child.exitCode==null)child.kill(); }
  }
})().catch((error)=>{
  fs.mkdirSync(SHOTS,{recursive:true});
  fs.writeFileSync(path.join(SHOTS,'TEST-OUTPUT.txt'),String(error.stack||error));
  console.error(error.stack||error);
  process.exit(1);
});
