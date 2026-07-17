const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '..');
const release = path.join(ROOT, 'release');
const script = path.join(__dirname, 'GoldSlots-Player-6.9.3.nsi');
const output = path.join(release, 'GoldSlots-Player-Setup-6.9.3.exe');
const candidates = [
  process.env.MAKENSIS,
  'makensis.exe',
  'C:\\Program Files (x86)\\NSIS\\makensis.exe',
  'C:\\Program Files\\NSIS\\makensis.exe',
  'C:\\ProgramData\\chocolatey\\bin\\makensis.exe'
].filter(Boolean);

fs.mkdirSync(release, { recursive:true });
fs.rmSync(output, { force:true });
if (!fs.existsSync(script)) throw new Error(`NSIS script is missing: ${script}`);
if (!fs.existsSync(path.join(ROOT, 'work', 'prepackaged', 'Gold Slots Player.exe'))) throw new Error('Prepackaged Player executable is missing.');
if (!fs.existsSync(path.join(ROOT, 'work', 'prepackaged', 'resources', 'app.asar'))) throw new Error('Prepackaged Player app.asar is missing.');

let compiler = null;
for (const candidate of candidates) {
  if (candidate.toLowerCase() === 'makensis.exe') {
    const probe = spawnSync(candidate, ['/VERSION'], { encoding:'utf8', shell:false });
    if (!probe.error && probe.status === 0) { compiler = candidate; break; }
  } else if (fs.existsSync(candidate)) { compiler = candidate; break; }
}
if (!compiler) throw new Error('NSIS makensis.exe was not found on the Windows build runner.');

console.log(`Using NSIS compiler: ${compiler}`);
const result = spawnSync(compiler, ['/V4', script], {
  cwd:ROOT,
  encoding:'utf8',
  maxBuffer:80*1024*1024,
  windowsHide:true
});
const log = [result.stdout || '', result.stderr || '', result.error ? String(result.error.stack || result.error) : ''].join('\n');
fs.writeFileSync(path.join(release, 'BUILD-OUTPUT.txt'), log);
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
if (result.error) throw result.error;
if (result.status !== 0) throw new Error(`NSIS compiler exited with code ${result.status}.`);
if (!fs.existsSync(output)) throw new Error(`NSIS did not create the installer: ${output}`);
const size = fs.statSync(output).size;
if (size < 70_000_000) throw new Error(`Installer is unexpectedly small: ${size} bytes.`);
console.log(`Created ${output} (${size} bytes).`);
