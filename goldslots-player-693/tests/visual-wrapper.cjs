const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const sourcePath = path.join(__dirname, 'visual-installed.e2e.cjs');
const generatedPath = path.join(__dirname, '.visual-generated.cjs');
const proofDir = path.resolve(__dirname, '..', 'visual-proof', process.env.GS_TEST_LABEL || 'installed');
fs.mkdirSync(proofDir, { recursive: true });

let source = fs.readFileSync(sourcePath, 'utf8');
source = source.replace(
  'async function assertScreenshotVisual',
  `async function captureWindow(app, file, clip = null) {
  const normalizedClip = clip ? { x:Math.max(0,Math.floor(clip.x)), y:Math.max(0,Math.floor(clip.y)), width:Math.max(1,Math.ceil(clip.width)), height:Math.max(1,Math.ceil(clip.height)) } : null;
  const base64 = await app.evaluate(async ({ BrowserWindow }, value) => {
    const win = BrowserWindow.getAllWindows()[0];
    win.show();
    const image = value.clip ? await win.webContents.capturePage(value.clip) : await win.webContents.capturePage();
    return image.toPNG().toString('base64');
  }, { clip:normalizedClip });
  fs.writeFileSync(file, Buffer.from(base64, 'base64'));
}

async function assertScreenshotVisual`
);
source = source.replace(
  '    const page = await app.firstWindow();',
  "    const page = await app.firstWindow();\n    await page.addStyleTag({content:'*,*::before,*::after{animation:none!important;transition:none!important;caret-color:transparent!important}'});"
);
source = source.replace(
  "    await page.waitForSelector('.game-grid',{state:'attached',timeout:30000});",
  `    await page.waitForTimeout(2000);
    fs.writeFileSync(path.join(SHOTS,'BEFORE-LOBBY.html'),await page.content());
    fs.writeFileSync(path.join(SHOTS,'BEFORE-LOBBY-ERRORS.json'),JSON.stringify(pageErrors,null,2));
    await captureWindow(app,path.join(SHOTS,'before-lobby.png'));
    await page.waitForSelector('.game-grid',{state:'attached',timeout:10000});`
);
source = source.replace("await page.screenshot({path:path.join(SHOTS,'raw-first-lobby.png')});", "await captureWindow(app,path.join(SHOTS,'raw-first-lobby.png'));");
source = source.replace('await page.screenshot({path:shot});', 'await captureWindow(app,shot);');
source = source.replace("await page.screenshot({path:logoShot,clip:{x:Math.max(0,logoBox.x-3),y:Math.max(0,logoBox.y-3),width:logoBox.width+6,height:logoBox.height+6}});", "await captureWindow(app,logoShot,{x:Math.max(0,logoBox.x-3),y:Math.max(0,logoBox.y-3),width:logoBox.width+6,height:logoBox.height+6});");
source = source.replace('await page.screenshot({path:gameShot});', 'await captureWindow(app,gameShot);');
source = source.replace('})().catch((error)=>{', '})().then(()=>process.exit(0)).catch((error)=>{');
source = source.replace('})().catch((error) => {', '})().then(()=>process.exit(0)).catch((error) => {');
fs.writeFileSync(generatedPath, source);

const result = spawnSync(process.execPath, [generatedPath], {
  cwd: path.resolve(__dirname, '..'),
  env: process.env,
  encoding: 'utf8',
  timeout: 8 * 60 * 1000,
  maxBuffer: 20 * 1024 * 1024
});
const output = [result.stdout || '', result.stderr || '', result.error ? String(result.error.stack || result.error) : ''].join('\n');
fs.writeFileSync(path.join(proofDir, 'TEST-OUTPUT.txt'), output);
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
if (result.error) console.error(result.error);
process.exit(typeof result.status === 'number' ? result.status : 1);
