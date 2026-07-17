const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const asar = require('@electron/asar');

const ROOT = path.resolve(__dirname, '..');
const WORK = path.join(ROOT, 'work');
const BASE_INSTALL = process.env.BASE_INSTALL_DIR || 'C:\\GSP680BASE';
const PREPACKAGED = path.join(WORK, 'prepackaged');
const EXTRACTED = path.join(WORK, 'app-extracted');
const OVERLAY = path.join(ROOT, 'overlay');
const BUILD = path.join(ROOT, 'build');
const APPROVED = ['lucky-reels','five-card-poker','jacks-or-better','blackjack-21','keno','roulette','money-wheel','bingo','hi-lo-cards','baccarat','craps'];

function copyDir(source, target) {
  fs.rmSync(target, { recursive: true, force: true });
  fs.cpSync(source, target, { recursive: true, force: true });
}
function read(file) { return fs.readFileSync(file, 'utf8'); }
function write(file, value) { fs.mkdirSync(path.dirname(file), { recursive: true }); fs.writeFileSync(file, value); }
function replaceOptional(value, search, replacement) { return search instanceof RegExp ? value.replace(search, replacement) : value.replace(search, replacement); }
function requireFile(file, minBytes = 32) {
  if (!fs.existsSync(file) || fs.statSync(file).size < minBytes) throw new Error(`Required original visual asset is missing or incomplete: ${file}`);
}
function removeMatching(root, test) {
  for (const entry of fs.readdirSync(root, { withFileTypes: true })) {
    const file = path.join(root, entry.name);
    if (entry.isDirectory()) removeMatching(file, test);
    else if (test(file)) fs.rmSync(file, { force: true });
  }
}

(async () => {
  requireFile(BASE_INSTALL, 1);
  const sourceExe = path.join(BASE_INSTALL, 'Gold Slots Player.exe');
  const sourceAsar = path.join(BASE_INSTALL, 'resources', 'app.asar');
  requireFile(sourceExe, 1000000);
  requireFile(sourceAsar, 1000000);

  console.log('Copying the original installed Gold Slots Player runtime.');
  copyDir(BASE_INSTALL, PREPACKAGED);
  const appAsar = path.join(PREPACKAGED, 'resources', 'app.asar');
  fs.rmSync(EXTRACTED, { recursive: true, force: true });
  fs.mkdirSync(EXTRACTED, { recursive: true });
  asar.extractAll(appAsar, EXTRACTED);

  const originalVisuals = [
    'assets/brand/goldslots-logo.svg',
    'assets/brand/goldslots-logo.png',
    'dist/goldslots-brand.css',
    'dist/public-release.css',
    'dist/casino-premium-3d.css',
    ...APPROVED.map((key) => `assets/game-menu/${key}.webp`),
    ...APPROVED.map((key) => `assets/game-screens/${key}.webp`)
  ];
  for (const rel of originalVisuals) requireFile(path.join(EXTRACTED, rel), rel.endsWith('.webp') ? 8000 : 100);

  console.log('Applying strict manual controls and responsive layout on top of the original visual package.');
  const dist = path.join(EXTRACTED, 'dist');
  write(path.join(dist, 'manual-693.js'), read(path.join(OVERLAY, 'manual-693.part1.js')) + read(path.join(OVERLAY, 'manual-693.part2.js')));
  fs.copyFileSync(path.join(OVERLAY, 'manual-693.css'), path.join(dist, 'manual-693.css'));
  fs.copyFileSync(path.join(OVERLAY, 'release-693.css'), path.join(dist, 'release-693.css'));
  fs.copyFileSync(path.join(OVERLAY, 'release-693.js'), path.join(dist, 'release-693.js'));

  const pkgPath = path.join(EXTRACTED, 'package.json');
  const pkg = JSON.parse(read(pkgPath));
  pkg.version = '6.9.3';
  pkg.productName = 'Gold Slots Player';
  pkg.description = 'Gold Slots Player 6.9.3 original premium visual release with native clicks, strict manual decisions and responsive casino-terminal layouts.';
  write(pkgPath, `${JSON.stringify(pkg, null, 2)}\n`);

  let preload = read(path.join(EXTRACTED, 'preload.js'));
  if (!preload.includes("GS_PLAYER_UI_TEST")) {
    preload = preload.replace(
      "const { contextBridge, ipcRenderer } = require('electron');",
      "const { contextBridge, ipcRenderer } = require('electron');\nif (process.env.GS_PLAYER_UI_TEST === '1') contextBridge.exposeInMainWorld('__GC_UI_TEST__', true);"
    );
  }
  write(path.join(EXTRACTED, 'preload.js'), preload);

  let main = read(path.join(EXTRACTED, 'main.js'));
  main = main.replace(/6\.(?:7|8|9)\.\d+/g, '6.9.3');
  if (!main.includes('app.disableHardwareAcceleration()')) {
    main = main.replace(
      "const { app, BrowserWindow, ipcMain, safeStorage, net, screen } = require('electron');",
      "const { app, BrowserWindow, ipcMain, safeStorage, net, screen } = require('electron');\napp.disableHardwareAcceleration();"
    );
  }
  main = main.replace(/kioskLocked:\s*raw\.kioskLocked\s*===\s*true/g, 'kioskLocked: false');
  main = main.replace(/terminalDisabled:\s*raw\.terminalDisabled\s*===\s*true/g, 'terminalDisabled: false');
  main = main.replace(/kioskLockEnabled\s*=\s*initialPolicy\.kioskLocked\s*===\s*true\s*;/g, 'kioskLockEnabled = false;');
  main = main.replace(/fullscreen:\s*kioskLockEnabled/g, 'fullscreen: false');
  main = main.replace(/kiosk:\s*kioskLockEnabled/g, 'kiosk: false');
  main = main.replace(/alwaysOnTop:\s*kioskLockEnabled/g, 'alwaysOnTop: false');
  main = main.replace(/skipTaskbar:\s*kioskLockEnabled/g, 'skipTaskbar: false');
  main = main.replace(/devTools:\s*!app\.isPackaged/g, "devTools: !app.isPackaged || process.env.GS_PLAYER_UI_TEST === '1'");
  if (!main.includes('writeConfig({ kioskLocked:false, terminalDisabled:false })') && main.includes('const initialPolicy = readConfig();')) {
    main = main.replace('const initialPolicy = readConfig();', "const initialPolicy = readConfig();\n  writeConfig({ kioskLocked:false, terminalDisabled:false });");
  }
  main = main.replace(/function startRfid\(\)\s*\{[\s\S]*?\n\}\n\nfunction forceKioskFocus/, `function startRfid() {
  stopRfid();
  return { ok: true, mode: 'USB_HID', message: 'USB HID / keyboard-emulation card reader ready.' };
}

function forceKioskFocus`);
  write(path.join(EXTRACTED, 'main.js'), main);

  let renderer = read(path.join(dist, 'renderer.js'));
  renderer = renderer.replace(/V6\.(?:7|8|9)\.\d+/g, 'V6.9.3');
  const gamesBlock = `const GAMES = [
  ['lucky-reels','LUCKY 7 SLOTS','Classic 7, BAR and cherry reels','slot','ruby'],
  ['five-card-poker','FIVE-CARD POKER','Casino deal and draw','poker','felt'],
  ['jacks-or-better','JACKS OR BETTER','Hold cards, then draw once','poker','felt'],
  ['blackjack-21','BLACKJACK','Build closer to 21 than the dealer','blackjack','midnight'],
  ['keno','KENO','Choose numbers from 1 to 80','keno','violet'],
  ['roulette','ROULETTE','European single-zero wheel','roulette','roulette'],
  ['money-wheel','MONEY WHEEL','Pick a segment multiplier','wheel','sapphire'],
  ['bingo','BINGO','Premium 75-ball draw','bingo','crimson'],
  ['hi-lo-cards','HI-LO CARD','Call the next card higher or lower','hilo','crimson'],
  ['baccarat','BACCARAT','Player · Banker · Tie','baccarat','baccarat'],
  ['craps','CRAPS','Pass-line come-out dice','craps','felt']
].map(([key,name,tagline,type,theme])=>({key,name,tagline,type,theme}));`;
  renderer = renderer.replace(/const GAMES = \[[\s\S]*?\]\.map\(\(\[key,name,tagline,type,theme\]\)=>\(\{key,name,tagline,type,theme\}\)\);/, gamesBlock);
  renderer = renderer.replace(/betCents:\s*(?:100|allowedBets\(\)\[0\]\|\|100)/g, 'betCents:0');
  if (!renderer.includes("window.__GC_UI_TEST__===true")) throw new Error('The original visual renderer does not contain its visual QA mode.');
  write(path.join(dist, 'renderer.js'), renderer);

  let index = read(path.join(dist, 'index.html'));
  index = index.replace(/\s*<link[^>]+href="(?:manual|release|click)-\d+\.css"[^>]*>/g, '');
  index = index.replace(/\s*<script[^>]+src="(?:manual|release|click)-\d+\.js"[^>]*><\/script>/g, '');
  index = index.replace('</head>', '  <link rel="stylesheet" href="manual-693.css">\n  <link rel="stylesheet" href="release-693.css">\n</head>');
  index = index.replace('</body>', '<script src="manual-693.js"></script><script src="release-693.js"></script></body>');
  if (!index.includes('../assets/brand/goldslots-logo.svg')) throw new Error('The original Gold Slots logo reference was not preserved.');
  for (const requiredCss of ['goldslots-brand.css','public-release.css','casino-premium-3d.css','manual-693.css','release-693.css']) {
    if (!index.includes(requiredCss)) throw new Error(`Required visual stylesheet missing from index: ${requiredCss}`);
  }
  write(path.join(dist, 'index.html'), index);

  removeMatching(EXTRACTED, (file) => /(?:click-687|click-\d+)\.(?:js|css)$/i.test(file) || /\.ps1$/i.test(file));
  for (const rel of originalVisuals) requireFile(path.join(EXTRACTED, rel), rel.endsWith('.webp') ? 8000 : 100);
  requireFile(path.join(dist, 'manual-693.js'), 20000);
  requireFile(path.join(dist, 'release-693.css'), 5000);

  console.log('Repacking original visual application.');
  fs.rmSync(appAsar, { force: true });
  await asar.createPackage(EXTRACTED, appAsar);
  requireFile(appAsar, 1000000);

  fs.mkdirSync(BUILD, { recursive: true });
  const logoPng = path.join(EXTRACTED, 'assets', 'brand', 'goldslots-logo.png');
  fs.copyFileSync(logoPng, path.join(BUILD, 'icon.png'));
  const module = await import('png-to-ico');
  const pngToIco = module.default || module;
  fs.writeFileSync(path.join(BUILD, 'icon.ico'), await pngToIco(logoPng));
  requireFile(path.join(BUILD, 'icon.ico'), 5000);

  const summary = {
    version: '6.9.3',
    originalVisualAssets: originalVisuals.length,
    approvedGameImages: APPROVED.length,
    appAsarBytes: fs.statSync(appAsar).size,
    appAsarSha256: crypto.createHash('sha256').update(fs.readFileSync(appAsar)).digest('hex'),
    oldClickRescueFiles: 0,
    powershellFiles: 0
  };
  write(path.join(WORK, 'PATCH-SUMMARY.json'), `${JSON.stringify(summary, null, 2)}\n`);
  console.log(JSON.stringify(summary, null, 2));
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
