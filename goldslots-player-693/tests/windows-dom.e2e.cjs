const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { _electron: electron } = require('playwright-core');

const ROOT = path.resolve(__dirname, '..');
const EXE = process.env.GS_PLAYER_EXE;
const LABEL = process.env.GS_TEST_LABEL || 'installed';
const PROOF = path.join(ROOT, 'visual-proof', LABEL);
const GAMES = ['lucky-reels','five-card-poker','jacks-or-better','blackjack-21','keno','roulette','money-wheel','bingo','hi-lo-cards','baccarat','craps'];
if (!EXE) throw new Error('GS_PLAYER_EXE is required.');
fs.mkdirSync(PROOF, { recursive:true });

async function setSize(app, page, cdp, width, height) {
  await app.evaluate(({ BrowserWindow }, value) => {
    const win = BrowserWindow.getAllWindows()[0];
    win.show();
    win.unmaximize();
    win.setKiosk(false);
    win.setFullScreen(false);
    win.setAlwaysOnTop(false);
    win.setSize(Math.min(value.width, 1024), Math.min(value.height, 720), false);
    win.center();
  }, { width, height });
  await cdp.send('Emulation.setDeviceMetricsOverride', {
    width,
    height,
    deviceScaleFactor:1,
    mobile:false,
    screenWidth:width,
    screenHeight:height,
    screenOrientation:{ type:height > width ? 'portraitPrimary' : 'landscapePrimary', angle:0 }
  });
  await page.evaluate(() => {
    window.dispatchEvent(new Event('resize'));
    window.dispatchEvent(new Event('orientationchange'));
  });
}

async function nativeClick(page, locator, label) {
  await locator.waitFor({ state:'visible', timeout:10000 });
  const box = await locator.boundingBox();
  assert.ok(box && box.width > 2 && box.height > 2, `${label} must have a native clickable area.`);
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  await page.mouse.down();
  await page.mouse.up();
  await page.waitForTimeout(90);
}

async function layoutSnapshot(page) {
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
        backgroundImage:style.backgroundImage,
        gridRows:style.gridTemplateRows,
        gridColumns:style.gridTemplateColumns
      };
    };
    return {
      title:document.title,
      readyState:document.readyState,
      visibilityState:document.visibilityState,
      viewport:{width:innerWidth,height:innerHeight},
      shell:pick('.shell'), content:pick('.content'), lobby:pick('.lobby'), grid:pick('.game-grid'), logo:pick('.gc-mark'), firstCard:pick('.game-tile')
    };
  });
}

async function verifyLobby(page, width, height) {
  await page.waitForSelector('.game-grid', { state:'attached', timeout:15000 });
  const layout = await layoutSnapshot(page);
  fs.writeFileSync(path.join(PROOF, `layout-${width}x${height}.json`), JSON.stringify(layout, null, 2));
  assert.match(layout.title, /Gold Slots Player/i, 'Window title must identify Gold Slots Player.');
  assert.ok(layout.grid?.rect.width > 250 && layout.grid?.rect.height > 180, `Lobby grid must have a visible area: ${JSON.stringify(layout.grid)}`);
  assert.ok(layout.logo?.rect.width >= 36 && layout.logo?.rect.height >= 36, 'Gold Slots logo must have a visible area.');
  assert.match(layout.logo?.backgroundImage || '', /goldslots-logo/i, 'Header must use the original Gold Slots logo asset.');

  const cards = page.locator('button.game-tile');
  assert.equal(await cards.count(), 11, 'Exactly eleven approved original game cards must render.');
  const images = await page.locator('button.game-tile img').evaluateAll((nodes) => nodes.map((node) => ({
    src:node.getAttribute('src'), complete:node.complete, naturalWidth:node.naturalWidth, naturalHeight:node.naturalHeight
  })));
  assert.equal(images.length, 11, 'Every approved card must contain its original image.');
  for (const [index, image] of images.entries()) {
    assert.ok(image.complete, `Lobby image ${index + 1} must finish loading.`);
    assert.ok(image.naturalWidth >= 200 && image.naturalHeight >= 275, `Lobby image ${index + 1} must be real artwork, not an empty placeholder.`);
    assert.match(image.src || '', /assets\/game-menu\/.+\.webp$/i, `Lobby image ${index + 1} must use assets/game-menu artwork.`);
  }

  const styles = await page.evaluate(() => [...document.styleSheets].map((sheet) => sheet.href || 'inline'));
  for (const name of ['goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.css','release-693.css']) {
    assert.ok(styles.some((value) => value.includes(name)), `${name} must remain loaded.`);
  }
  const scripts = await page.evaluate(() => [...document.scripts].map((script) => script.src || 'inline'));
  for (const name of ['manual-693.js','release-693.js']) {
    assert.ok(scripts.some((value) => value.includes(name)), `${name} must execute in the original design.`);
  }

  const viewport = layout.viewport;
  assert.ok(Math.abs(viewport.width - width) < 5, `Expected responsive width ${width}; received ${viewport.width}.`);
  assert.ok(Math.abs(viewport.height - height) < 5, `Expected responsive height ${height}; received ${viewport.height}.`);
  const boxes = [];
  for (let index = 0; index < 11; index += 1) {
    const box = await cards.nth(index).boundingBox();
    assert.ok(box && box.width > 55 && box.height > 45, `Game card ${index + 1} must have a clickable area.`);
    assert.ok(box.x >= -1 && box.y >= -1 && box.x + box.width <= viewport.width + 2 && box.y + box.height <= viewport.height + 2, `Game card ${index + 1} must fit inside the window.`);
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
  return { layout, images, styles, scripts };
}

(async () => {
  const app = await electron.launch({
    executablePath:EXE,
    args:['--disable-gpu'],
    cwd:path.dirname(EXE),
    env:{...process.env,GS_PLAYER_UI_TEST:'1',ELECTRON_DISABLE_SECURITY_WARNINGS:'true'},
    timeout:45000
  });
  let childProcess = null;
  try {
    try { childProcess = app.process(); } catch {}
    const page = await app.firstWindow();
    const cdp = await page.context().newCDPSession(page);
    page.setDefaultTimeout(10000);
    page.setDefaultNavigationTimeout(10000);
    const errors=[];
    page.on('pageerror',(error)=>errors.push(error.message));
    page.on('console',(message)=>console.log(`[renderer:${message.type()}] ${message.text()}`));
    await page.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important}'});
    await page.waitForSelector('.game-grid',{state:'attached',timeout:15000});

    const proof = {};
    for (const [width,height] of [[1024,576],[1366,768],[1920,1080],[800,1000]]) {
      await setSize(app,page,cdp,width,height);
      await page.waitForTimeout(220);
      proof[`${width}x${height}`]=await verifyLobby(page,width,height);
    }

    await setSize(app,page,cdp,1024,576);
    await page.waitForTimeout(220);
    for (const key of GAMES) {
      await nativeClick(page,page.locator(`button.game-tile[data-key="${key}"]`),`${key} lobby card`);
      await page.waitForSelector('.game-shell',{state:'attached'});
      const stage = await page.evaluate((gameKey) => {
        const shell=document.querySelector('.game-shell');
        const stageNode=document.querySelector('.game-stage');
        const rect=stageNode?.getBoundingClientRect();
        const images=[...document.images].map((image)=>({src:image.getAttribute('src'),complete:image.complete,width:image.naturalWidth,height:image.naturalHeight}));
        const styled=[...document.querySelectorAll('.game-shell *')].map((node)=>getComputedStyle(node).backgroundImage).find((value)=>value&&value.includes('game-screens'))||'';
        return {gameKey,shell:Boolean(shell),rect:rect?{width:rect.width,height:rect.height}:null,images,styled,title:document.querySelector('.game-title h1')?.textContent||''};
      }, key);
      assert.ok(stage.shell && stage.rect?.width > 300 && stage.rect?.height > 180, `${key} original game stage must be visible.`);
      assert.ok(stage.images.some((image)=>image.complete&&image.width>0&&image.height>0) || /game-screens/i.test(stage.styled), `${key} must retain rendered visual assets.`);
      fs.writeFileSync(path.join(PROOF,`game-${key}.json`),JSON.stringify(stage,null,2));
      await nativeClick(page,page.locator('[data-action="lobby"]'),'Return to lobby');
      await page.waitForSelector('.game-grid',{state:'attached'});
    }

    await nativeClick(page,page.locator('button.game-tile[data-key="hi-lo-cards"]'),'Hi-Lo lobby card');
    assert.equal(await page.locator('.play-action').isDisabled(),true,'Hi-Lo must start without a wager or automatic choice.');
    await nativeClick(page,page.locator('[data-action="bet"]').first(),'Hi-Lo stake');
    assert.equal(await page.locator('.play-action').isDisabled(),true,'Stake alone must not choose Higher or Lower.');
    await nativeClick(page,page.locator('[data-action="choice"][data-key="hiLoPick"][data-value="higher"]'),'Hi-Lo Higher');
    assert.equal(await page.locator('.play-action').isEnabled(),true,'Manual Higher choice must enable Hi-Lo play.');
    await nativeClick(page,page.locator('[data-action="lobby"]'),'Return to lobby');

    await page.evaluate(()=>{state.demoSession=true;state.demoBalanceCents=500000;Math.random=()=>0.31;render(true);});
    await page.waitForSelector('.game-grid',{state:'attached'});
    await nativeClick(page,page.locator('button.game-tile[data-key="blackjack-21"]'),'Blackjack lobby card');
    await nativeClick(page,page.locator('[data-action="bet"]').first(),'Blackjack stake');
    await nativeClick(page,page.locator('.play-action'),'Blackjack Deal');
    await page.waitForTimeout(350);
    if(await page.locator('[data-action="blackjack-hit"]').count()){
      await nativeClick(page,page.locator('[data-action="blackjack-hit"]'),'Blackjack Hit');
      await page.waitForTimeout(250);
      if(await page.locator('[data-action="blackjack-stand"]').count())await nativeClick(page,page.locator('[data-action="blackjack-stand"]'),'Blackjack Stand');
    }
    await page.waitForTimeout(300);
    assert.equal(await page.locator('.round-result').count(),1,'Blackjack Deal and manual Hit/Stand must complete.');
    await nativeClick(page,page.locator('[data-action="lobby"]'),'Return to lobby');

    await nativeClick(page,page.locator('button.game-tile[data-key="jacks-or-better"]'),'Jacks or Better lobby card');
    await nativeClick(page,page.locator('[data-action="bet"]').first(),'Poker stake');
    await nativeClick(page,page.locator('.play-action'),'Poker Deal');
    await page.waitForSelector('[data-action="poker-hold"]');
    await nativeClick(page,page.locator('[data-action="poker-hold"]').first(),'Poker Hold');
    assert.ok((await page.locator('[data-action="poker-hold"]').first().getAttribute('class')).includes('held'),'Poker Hold must remain visibly selected.');
    await nativeClick(page,page.locator('[data-action="poker-draw"]'),'Poker Draw');
    await page.waitForTimeout(400);
    assert.equal(await page.locator('[data-action="poker-hold"]').count(),0,'Poker Draw must finish the hand.');
    await nativeClick(page,page.locator('[data-action="lobby"]'),'Return to lobby');

    await nativeClick(page,page.locator('[data-action="sound-open"]'),'Open sound mixer');
    await page.waitForSelector('.sound-mixer',{state:'attached'});
    await nativeClick(page,page.locator('[data-action="sound-toggle"][data-key="effects"]'),'Toggle effects sound');
    await nativeClick(page,page.locator('[data-action="sound-close"]'),'Close sound mixer');
    assert.equal(await page.locator('.sound-mixer').count(),0,'Sound mixer must open, respond, and close.');

    const windowState=await app.evaluate(({BrowserWindow})=>{const win=BrowserWindow.getAllWindows()[0];return{kiosk:win.isKiosk(),fullscreen:win.isFullScreen(),alwaysOnTop:win.isAlwaysOnTop()};});
    assert.deepEqual(windowState,{kiosk:false,fullscreen:false,alwaysOnTop:false},'Player must start normally until fresh Super Admin kiosk policy arrives.');
    assert.deepEqual(errors,[],`Renderer errors are not allowed: ${errors.join(' | ')}`);
    fs.writeFileSync(path.join(PROOF,'WINDOWS-DOM-TEST-PASS.json'),JSON.stringify({label:LABEL,games:GAMES,resolutions:Object.keys(proof),windowState,errors},null,2));
    console.log(`PASS: ${LABEL} original Gold Slots logo, eleven lobby artworks, premium styles, responsive bounds, native clicks, Hi-Lo, Blackjack, Poker and sound.`);
  } finally {
    try { await Promise.race([app.close(),new Promise((resolve)=>setTimeout(resolve,5000))]); } catch {}
    if(childProcess&&childProcess.exitCode==null){try{childProcess.kill();}catch{}}
  }
})().then(()=>process.exit(0)).catch((error)=>{
  fs.writeFileSync(path.join(PROOF,'TEST-OUTPUT.txt'),String(error.stack||error));
  console.error(error.stack||error);
  process.exit(1);
});
