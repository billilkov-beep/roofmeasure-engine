const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');

const ROOT = path.resolve(__dirname, '..');
const WORK = path.join(ROOT, 'work');
const PROJECT = 'zcrcioiuhrbxinvhqnfm';
const BUCKET = 'gc-build-delivery-temp';
const PREFIX = '6.8.0/GoldSlots-Player-Setup-6.8.0.exe';
const MANIFEST = `${PREFIX}.manifest.json`;

const sha256 = (bytes) => crypto.createHash('sha256').update(bytes).digest('hex');
const encodePath = (value) => String(value).split('/').map(encodeURIComponent).join('/');

async function fetchObject(objectName) {
  const encoded = encodePath(objectName);
  const urls = [
    `https://${PROJECT}.supabase.co/storage/v1/object/public/${BUCKET}/${encoded}`,
    `https://${PROJECT}.supabase.co/storage/v1/object/${BUCKET}/${encoded}`
  ];
  let last = '';
  for (const url of urls) {
    for (let attempt = 1; attempt <= 3; attempt += 1) {
      try {
        const response = await fetch(url, { redirect: 'follow' });
        if (response.ok) return Buffer.from(await response.arrayBuffer());
        last = `${response.status} ${await response.text()}`;
      } catch (error) {
        last = error.message || String(error);
      }
      await new Promise((resolve) => setTimeout(resolve, attempt * 1000));
    }
  }
  throw new Error(`Unable to download ${objectName}: ${last}`);
}

(async () => {
  fs.mkdirSync(WORK, { recursive: true });
  console.log(`Downloading original visual manifest: ${MANIFEST}`);
  const manifestBytes = await fetchObject(MANIFEST);
  const manifest = JSON.parse(manifestBytes.toString('utf8'));
  if (manifest.schema !== 'goldslots.chunked-installer.v1') throw new Error('Unexpected base installer manifest schema.');
  if (!Array.isArray(manifest.parts) || manifest.parts.length < 8) throw new Error('Base installer manifest does not contain the expected parts.');
  const parts = [];
  for (const [index, entry] of manifest.parts.entries()) {
    console.log(`Downloading original visual part ${index + 1}/${manifest.parts.length}: ${entry.name}`);
    const bytes = await fetchObject(entry.name);
    if (Number(entry.size) !== bytes.length) throw new Error(`Part size mismatch for ${entry.name}.`);
    if (String(entry.sha256).toLowerCase() !== sha256(bytes)) throw new Error(`Part SHA-256 mismatch for ${entry.name}.`);
    parts.push(bytes);
  }
  const installer = Buffer.concat(parts);
  if (Number(manifest.fileSizeBytes) !== installer.length) throw new Error('Original visual installer size does not match its manifest.');
  const digest = sha256(installer);
  if (String(manifest.sha256).toLowerCase() !== digest) throw new Error('Original visual installer SHA-256 does not match its manifest.');
  const output = path.join(WORK, 'GoldSlots-Player-Setup-6.8.0-Original-Visual.exe');
  fs.writeFileSync(output, installer);
  fs.writeFileSync(path.join(WORK, 'BASE-MANIFEST.json'), JSON.stringify(manifest, null, 2));
  fs.writeFileSync(path.join(WORK, 'BASE-SHA256.txt'), `${digest}\n`);
  console.log(`Verified original visual installer: ${output}`);
  console.log(`SHA256=${digest}`);
  console.log(`SIZE=${installer.length}`);
})().catch((error) => {
  console.error(error.stack || error);
  process.exit(1);
});
