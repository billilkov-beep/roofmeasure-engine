const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const sourcePath = path.join(__dirname, 'visual-installed.e2e.cjs');
const generatedPath = path.join(__dirname, '.visual-generated.cjs');
const proofDir = path.resolve(__dirname, '..', 'visual-proof', process.env.GS_TEST_LABEL || 'installed');
fs.mkdirSync(proofDir, { recursive: true });

let source = fs.readFileSync(sourcePath, 'utf8');
source = source.replace('})().catch((error)=>{', '})().then(()=>process.exit(0)).catch((error)=>{');
source = source.replace('})().catch((error) => {', '})().then(()=>process.exit(0)).catch((error) => {');
fs.writeFileSync(generatedPath, source);

const result = spawnSync(process.execPath, [generatedPath], {
  cwd: path.resolve(__dirname, '..'),
  env: process.env,
  encoding: 'utf8',
  timeout: 6 * 60 * 1000,
  maxBuffer: 20 * 1024 * 1024
});
const output = [result.stdout || '', result.stderr || '', result.error ? String(result.error.stack || result.error) : ''].join('\n');
fs.writeFileSync(path.join(proofDir, 'TEST-OUTPUT.txt'), output);
process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
if (result.error) console.error(result.error);
process.exit(typeof result.status === 'number' ? result.status : 1);
