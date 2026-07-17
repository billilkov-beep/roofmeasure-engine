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

(async () => {
  const app = await electron.launch({
    executablePath:EXE,
    args:['--disable-gpu'],
    cwd:path.dirname(EXE),
    env:{ ...process.env, GS_PLAYER_UI_TEST:'1', ELECTRON_DISABLE_SECURITY_WARNINGS:'true' },
    timeout:45000
  });
  let child=null;
  try {
    try{child=app.process();}catch{}
    const page=await app.firstWindow();
    page.setDefaultTimeout(10000);
    const errors=[];
    page.on('pageerror',(error)=>errors.push(error.message));
    await page.waitForSelector('.game-grid',{state:'attached',timeout:15000});

    const lobby=await page.evaluate(()=>{
      const box=(selector)=>{const node=document.querySelector(selector);if(!node)return null;const r=node.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};};
      return{
        title:document.title,
        cards:document.querySelectorAll('button.game-tile').length,
        grid:box('.game-grid'),
        logo:{box:box('.gc-mark'),background:getComputedStyle(document.querySelector('.gc-mark')).backgroundImage},
        images:[...document.querySelectorAll('button.game-tile img')].map((image)=>({src:image.getAttribute('src'),complete:image.complete,width:image.naturalWidth,height:image.naturalHeight})),
        styles:[...document.styleSheets].map((sheet)=>sheet.href||'inline'),
        scripts:[...document.scripts].map((script)=>script.src||'inline'),
        testApi:Boolean(window.__gcTest&&typeof window.__gcTest.selectGame==='function')
      };
    });
    assert.match(lobby.title,/Gold Slots Player/i);
    assert.equal(lobby.cards,11);
    assert.ok(lobby.grid?.width>500&&lobby.grid?.height>180);
    assert.ok(lobby.logo.box?.width>=36&&lobby.logo.box?.height>=36);
    assert.match(lobby.logo.background,/goldslots-logo/i);
    assert.equal(lobby.images.length,11);
    for(const image of lobby.images){assert.ok(image.complete&&image.width>=200&&image.height>=275,`Missing original artwork: ${JSON.stringify(image)}`);assert.match(image.src||'',/assets\/game-menu\/.+\.webp$/i);}
    for(const name of ['goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.css','release-693.css'])assert.ok(lobby.styles.some((value)=>value.includes(name)),`${name} must load.`);
    for(const name of ['manual-693.js','release-693.js'])assert.ok(lobby.scripts.some((value)=>value.includes(name)),`${name} must load.`);
    assert.equal(lobby.testApi,true);

    const releaseCode=fs.readFileSync(path.join(ROOT,'work','app-extracted','dist','release-693.js'),'utf8');
    const pointerBridge={
      trusted:/event\.isTrusted/.test(releaseCode),
      pointer:/pointerdown/.test(releaseCode),
      hitTest:/elementsFromPoint/.test(releaseCode),
      activate:/control\.click\(\)/.test(releaseCode),
      duplicateGuard:/stopImmediatePropagation/.test(releaseCode)
    };
    assert.ok(Object.values(pointerBridge).every(Boolean),`Physical pointer bridge incomplete: ${JSON.stringify(pointerBridge)}`);

    const stages=[];
    for(const key of GAMES){
      const result=await page.evaluate((gameKey)=>{
        window.__gcTest.selectGame(gameKey);
        const shell=document.querySelector('.game-shell');
        const stage=document.querySelector('.game-stage');
        const r=stage?.getBoundingClientRect();
        const background=getComputedStyle(shell).getPropertyValue('--game-backdrop')||getComputedStyle(stage).backgroundImage||'';
        return{key:gameKey,shell:Boolean(shell),stage:r?{width:r.width,height:r.height}:null,background,title:document.querySelector('.game-title h1')?.textContent||''};
      },key);
      assert.ok(result.shell&&result.stage?.width>300&&result.stage?.height>100,`${key} stage must render.`);
      stages.push(result);
      await page.evaluate(()=>{state.game=null;state.result=null;state.revealResult=false;render();});
      await page.waitForSelector('.game-grid',{state:'attached',timeout:5000});
    }

    const manual=fs.readFileSync(path.join(ROOT,'work','app-extracted','dist','manual-693.js'),'utf8');
    for(const token of ['BLACKJACK_HIT','BLACKJACK_STAND','POKER_DEAL','POKER_DRAW','hiLoPick','manual.betSelected'])assert.ok(manual.includes(token),`Manual control token missing: ${token}`);
    assert.ok(!/dispatchEvent\s*\(|new\s+MouseEvent/.test(manual),'Manual gameplay layer must not contain synthetic repeat-click code.');

    const windowState=await app.evaluate(({BrowserWindow})=>{const win=BrowserWindow.getAllWindows()[0];return{kiosk:win.isKiosk(),fullscreen:win.isFullScreen(),alwaysOnTop:win.isAlwaysOnTop()};});
    assert.deepEqual(windowState,{kiosk:false,fullscreen:false,alwaysOnTop:false});
    assert.deepEqual(errors,[],`Renderer errors: ${errors.join(' | ')}`);

    const result={label:LABEL,version:'6.9.3',originalLobbyImages:lobby.images.length,games:stages,pointerBridge,windowState,errors};
    fs.writeFileSync(path.join(PROOF,'RELEASE-SAFE-SMOKE-PASS.json'),JSON.stringify(result,null,2));
    console.log(`PASS: ${LABEL} original visual Player, eleven stages, pointer bridge, manual controls and normal startup.`);
  }finally{
    try{await Promise.race([app.close(),new Promise((resolve)=>setTimeout(resolve,5000))]);}catch{}
    if(child&&child.exitCode==null){try{child.kill();}catch{}}
  }
})().then(()=>process.exit(0)).catch((error)=>{
  fs.writeFileSync(path.join(PROOF,'RELEASE-SAFE-SMOKE-ERROR.txt'),String(error.stack||error));
  console.error(error.stack||error);
  process.exit(1);
});
