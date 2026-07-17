const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '..');
const APP = path.join(ROOT, 'work', 'app-extracted');
const PREPACKAGED = path.join(ROOT, 'work', 'prepackaged');
const DIST = path.join(APP, 'dist');
const GAMES = ['lucky-reels','five-card-poker','jacks-or-better','blackjack-21','keno','roulette','money-wheel','bingo','hi-lo-cards','baccarat','craps'];
const required = (file, min = 1) => {
  if (!fs.existsSync(file) || fs.statSync(file).size < min) throw new Error(`Missing or incomplete release file: ${file}`);
};
const walk = (dir, list = []) => {
  for (const item of fs.readdirSync(dir, { withFileTypes:true })) {
    const file = path.join(dir, item.name);
    if (item.isDirectory()) walk(file, list); else list.push(file);
  }
  return list;
};

for (const file of ['main.js','preload.js','package.json']) required(path.join(APP, file), 100);
for (const file of ['renderer.js','goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.js','manual-693.css','release-693.js','release-693.css','index.html']) required(path.join(DIST, file), 100);
required(path.join(APP,'assets','brand','goldslots-logo.svg'),100);
required(path.join(APP,'assets','brand','goldslots-logo.png'),1000);
for(const game of GAMES){required(path.join(APP,'assets','game-menu',`${game}.webp`),8000);required(path.join(APP,'assets','game-screens',`${game}.webp`),8000);}

const index=fs.readFileSync(path.join(DIST,'index.html'),'utf8');
for(const token of ['goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.css','release-693.css','manual-693.js','release-693.js','goldslots-logo.svg']){
  if(!index.includes(token))throw new Error(`Index is missing original visual reference: ${token}`);
}
const manual=fs.readFileSync(path.join(DIST,'manual-693.js'),'utf8');
for(const token of ['BLACKJACK_HIT','BLACKJACK_STAND','POKER_DEAL','POKER_DRAW','hiLoPick','manual.betSelected']){
  if(!manual.includes(token))throw new Error(`Manual control is missing: ${token}`);
}
if(/dispatchEvent\s*\(|new\s+MouseEvent/.test(manual))throw new Error('Manual gameplay layer contains synthetic repeat-click code.');
const release=fs.readFileSync(path.join(DIST,'release-693.js'),'utf8');
for(const token of ['pointerdown','event.isTrusted','elementsFromPoint','control.click()','stopImmediatePropagation']){
  if(!release.includes(token))throw new Error(`Physical pointer bridge is missing: ${token}`);
}
const css=fs.readFileSync(path.join(DIST,'release-693.css'),'utf8');
for(const token of ['--gs-cols','--gs-rows','.game-grid','goldslots-logo.svg','orientation:portrait']){
  if(!css.includes(token))throw new Error(`Responsive visual rule is missing: ${token}`);
}
const ps1=walk(PREPACKAGED).filter((file)=>file.toLowerCase().endsWith('.ps1'));
if(ps1.length)throw new Error(`PowerShell runtime files found: ${ps1.join(', ')}`);

const summary={version:'6.9.3',logo:true,lobbyArtworks:GAMES.length,gameScreenArtworks:GAMES.length,premiumStyles:true,responsiveRules:true,physicalPointerBridge:true,manualControls:true,powerShellRuntimeFiles:0};
fs.writeFileSync(path.join(ROOT,'work','FINAL-VALIDATION.json'),JSON.stringify(summary,null,2));
console.log(JSON.stringify(summary,null,2));
