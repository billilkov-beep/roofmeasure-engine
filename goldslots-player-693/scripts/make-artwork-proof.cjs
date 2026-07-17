const fs = require('node:fs');
const path = require('node:path');
const sharp = require('sharp');

const ROOT = path.resolve(__dirname, '..');
const ASSETS = path.join(ROOT, 'work', 'app-extracted', 'assets');
const OUTPUT = path.join(ROOT, 'visual-proof', 'packaged-artwork');
const games = [
  ['lucky-reels','LUCKY 7 SLOTS'],['five-card-poker','FIVE-CARD POKER'],['jacks-or-better','JACKS OR BETTER'],
  ['blackjack-21','BLACKJACK'],['keno','KENO'],['roulette','ROULETTE'],['money-wheel','MONEY WHEEL'],
  ['bingo','BINGO'],['hi-lo-cards','HI-LO CARD'],['baccarat','BACCARAT'],['craps','CRAPS']
];
const escapeXml = (value) => String(value).replace(/[&<>"']/g, (char) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&apos;'}[char]));

(async () => {
  fs.mkdirSync(OUTPUT, { recursive:true });
  const canvasWidth = 1600;
  const canvasHeight = 1040;
  const tileWidth = 238;
  const tileHeight = 330;
  const gap = 18;
  const startX = 42;
  const startY = 230;
  const composites = [];

  const logoPath = path.join(ASSETS, 'brand', 'goldslots-logo.png');
  const logo = await sharp(logoPath).resize({ width:170, height:150, fit:'contain' }).png().toBuffer();
  composites.push({ input:logo, left:52, top:32 });
  const heading = Buffer.from(`<svg width="1320" height="160" xmlns="http://www.w3.org/2000/svg"><text x="20" y="65" fill="#ffe6a0" font-size="58" font-family="Georgia" font-weight="700">GOLD SLOTS PLAYER 6.9.3</text><text x="22" y="112" fill="#b8c8dc" font-size="25" font-family="Arial">Original packaged lobby artwork — 11 approved games</text><text x="22" y="147" fill="#77d7a5" font-size="20" font-family="Arial">Generated from the exact assets placed inside the Windows application package</text></svg>`);
  composites.push({ input:heading, left:230, top:36 });

  for (const [index,[key,label]] of games.entries()) {
    const row = Math.floor(index / 6);
    const column = index % 6;
    const left = startX + column * (tileWidth + gap);
    const top = startY + row * (tileHeight + 70);
    const file = path.join(ASSETS, 'game-menu', `${key}.webp`);
    const metadata = await sharp(file).metadata();
    if ((metadata.width || 0) < 200 || (metadata.height || 0) < 275) throw new Error(`Invalid artwork dimensions for ${key}.`);
    const image = await sharp(file).resize(tileWidth, tileHeight, { fit:'cover' }).png().toBuffer();
    const frame = Buffer.from(`<svg width="${tileWidth}" height="${tileHeight + 52}" xmlns="http://www.w3.org/2000/svg"><rect x="1" y="1" width="${tileWidth-2}" height="${tileHeight+50}" rx="15" fill="none" stroke="#d9ad58" stroke-width="2"/><rect x="0" y="${tileHeight-65}" width="${tileWidth}" height="117" fill="#030711" fill-opacity="0.92"/><text x="12" y="${tileHeight-25}" fill="#ffe6a0" font-size="17" font-family="Arial" font-weight="700">${escapeXml(label)}</text><text x="12" y="${tileHeight+17}" fill="#82d8ad" font-size="12" font-family="Arial">ORIGINAL WEBP · ${metadata.width}×${metadata.height}</text></svg>`);
    composites.push({ input:image, left, top });
    composites.push({ input:frame, left, top });
  }

  const background = {
    create:{ width:canvasWidth, height:canvasHeight, channels:4, background:{r:2,g:7,b:17,alpha:1} }
  };
  const output = path.join(OUTPUT, 'GoldSlots-6.9.3-Packaged-Artwork-Proof.png');
  await sharp(background).composite(composites).png({ compressionLevel:7 }).toFile(output);
  const stats = await sharp(output).stats();
  const variance = stats.channels.slice(0,3).reduce((sum,channel)=>sum+channel.stdev,0)/3;
  if (fs.statSync(output).size < 300000 || variance < 18) throw new Error('Artwork proof image is incomplete or visually flat.');
  fs.writeFileSync(path.join(OUTPUT, 'ARTWORK-PROOF.json'), JSON.stringify({ games:games.map(([key,label])=>({key,label})), file:path.basename(output), bytes:fs.statSync(output).size, averageChannelStdev:variance }, null, 2));
  console.log(`Created ${output}`);
})().catch((error)=>{console.error(error.stack||error);process.exit(1);});
