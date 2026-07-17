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

async function activate(page, selector) {
  const result = await page.evaluate((value) => {
    const control = document.querySelector(value);
    if (!(control instanceof HTMLElement)) return { ok:false, reason:'missing', selector:value };
    if (control.matches(':disabled') || control.getAttribute('aria-disabled') === 'true') return { ok:false, reason:'disabled', selector:value };
    const event = new MouseEvent('click', { bubbles:true, cancelable:true, view:window, button:0, buttons:0 });
    const dispatched = control.dispatchEvent(event);
    return { ok:true, dispatched, action:control.dataset.action || '', key:control.dataset.key || '', selector:value };
  }, selector);
  assert.equal(result.ok, true, `Control must activate: ${selector} (${JSON.stringify(result)})`);
  await page.waitForTimeout(120);
  return result;
}

(async () => {
  const app = await electron.launch({
    executablePath:EXE,
    args:['--disable-gpu'],
    cwd:path.dirname(EXE),
    env:{ ...process.env, GS_PLAYER_UI_TEST:'1', ELECTRON_DISABLE_SECURITY_WARNINGS:'true' },
    timeout:45000
  });
  let child = null;
  try {
    try { child = app.process(); } catch {}
    const page = await app.firstWindow();
    page.setDefaultTimeout(10000);
    const errors=[];
    page.on('pageerror',(error)=>errors.push(error.message));
    await page.waitForSelector('.game-grid',{state:'attached',timeout:15000});

    const lobby = await page.evaluate(() => {
      const rect = (selector) => {
        const node=document.querySelector(selector);
        if(!node)return null;
        const box=node.getBoundingClientRect();
        return {x:box.x,y:box.y,width:box.width,height:box.height};
      };
      return {
        title:document.title,
        cards:document.querySelectorAll('button.game-tile').length,
        logo:{rect:rect('.gc-mark'),background:getComputedStyle(document.querySelector('.gc-mark')).backgroundImage},
        grid:rect('.game-grid'),
        images:[...document.querySelectorAll('button.game-tile img')].map((image)=>({src:image.getAttribute('src'),complete:image.complete,width:image.naturalWidth,height:image.naturalHeight})),
        styles:[...document.styleSheets].map((sheet)=>sheet.href||'inline'),
        release:Boolean(window.GoldSlotsRelease),
        bridgeSource:[...document.scripts].some((script)=>/release-693\.js/i.test(script.src||''))
      };
    });
    assert.match(lobby.title,/Gold Slots Player/i);
    assert.equal(lobby.cards,11);
    assert.ok(lobby.logo.rect?.width>=36&&lobby.logo.rect?.height>=36);
    assert.match(lobby.logo.background,/goldslots-logo/i);
    assert.ok(lobby.grid?.width>500&&lobby.grid?.height>180);
    assert.equal(lobby.images.length,11);
    for(const image of lobby.images){
      assert.ok(image.complete&&image.width>=200&&image.height>=275,`Original lobby artwork failed: ${JSON.stringify(image)}`);
      assert.match(image.src||'',/assets\/game-menu\/.+\.webp$/i);
    }
    for(const name of ['goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.css','release-693.css']) assert.ok(lobby.styles.some((value)=>value.includes(name)),`${name} must load.`);
    assert.equal(lobby.release,true);
    assert.equal(lobby.bridgeSource,true);

    const releaseCode = fs.readFileSync(path.join(ROOT,'work','app-extracted','dist','release-693.js'),'utf8');
    const bridge = {
      trustedPointer:/pointerdown/.test(releaseCode)&&/event\.isTrusted/.test(releaseCode),
      hitTest:/elementsFromPoint/.test(releaseCode),
      activate:/control\.click\(\)/.test(releaseCode),
      duplicateGuard:/stopImmediatePropagation/.test(releaseCode)
    };
    assert.ok(Object.values(bridge).every(Boolean),`Physical pointer bridge is incomplete: ${JSON.stringify(bridge)}`);

    for(const key of GAMES){
      await activate(page,`button.game-tile[data-key="${key}"]`);
      await page.waitForSelector('.game-shell',{state:'attached',timeout:5000});
      const stage=await page.evaluate((gameKey)=>{
        const shell=document.querySelector('.game-shell');
        const stageNode=document.querySelector('.game-stage');
        const box=stageNode?.getBoundingClientRect();
        const styled=[...document.querySelectorAll('.game-shell *')].map((node)=>getComputedStyle(node).backgroundImage).find((value)=>/game-screens/i.test(value||''))||'';
        return {gameKey,shell:Boolean(shell),stage:box?{width:box.width,height:box.height}:null,styled};
      },key);
      assert.ok(stage.shell&&stage.stage?.width>300&&stage.stage?.height>100,`${key} stage must render.`);
      fs.writeFileSync(path.join(PROOF,`game-${key}.json`),JSON.stringify(stage,null,2));
      await activate(page,'[data-action="lobby"]');
      await page.waitForSelector('.game-grid',{state:'attached',timeout:5000});
    }

    await activate(page,'button.game-tile[data-key="hi-lo-cards"]');
    assert.equal(await page.locator('.play-action').isDisabled(),true);
    await activate(page,'[data-action="bet"]');
    assert.equal(await page.locator('.play-action').isDisabled(),true);
    await activate(page,'[data-action="choice"][data-key="hiLoPick"][data-value="higher"]');
    assert.equal(await page.locator('.play-action').isEnabled(),true);
    await activate(page,'[data-action="lobby"]');

    await activate(page,'[data-action="sound-open"]');
    await page.waitForSelector('.sound-mixer',{state:'attached',timeout:5000});
    await activate(page,'[data-action="sound-toggle"][data-key="effects"]');
    await activate(page,'[data-action="sound-close"]');
    assert.equal(await page.locator('.sound-mixer').count(),0);

    const windowState=await app.evaluate(({BrowserWindow})=>{const win=BrowserWindow.getAllWindows()[0];return{kiosk:win.isKiosk(),fullscreen:win.isFullScreen(),alwaysOnTop:win.isAlwaysOnTop()};});
    assert.deepEqual(windowState,{kiosk:false,fullscreen:false,alwaysOnTop:false});
    assert.deepEqual(errors,[],`Renderer errors: ${errors.join(' | ')}`);

    const result={label:LABEL,version:'6.9.3',games:GAMES,originalLobbyImages:lobby.images.length,bridge,windowState,errors};
    fs.writeFileSync(path.join(PROOF,'RELEASE-SMOKE-PASS.json'),JSON.stringify(result,null,2));
    console.log(`PASS: ${LABEL} original visual Player release smoke test.`);
  } finally {
    try { await Promise.race([app.close(),new Promise((resolve)=>setTimeout(resolve,5000))]); } catch {}
    if(child&&child.exitCode==null){try{child.kill();}catch{}}
  }
})().then(()=>process.exit(0)).catch((error)=>{
  fs.writeFileSync(path.join(PROOF,'RELEASE-SMOKE-ERROR.txt'),String(error.stack||error));
  console.error(error.stack||error);
  process.exit(1);
});
