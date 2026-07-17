const fs = require('node:fs');
const path = require('node:path');
const { Resvg } = require('@resvg/resvg-js');

(async () => {
  const root = path.resolve(__dirname, '..');
  const svg = fs.readFileSync(path.join(root, 'build', 'icon.svg'));
  const png = new Resvg(svg, { fitTo: { mode: 'width', value: 256 } }).render().asPng();
  const pngPath = path.join(root, 'build', 'icon.png');
  fs.writeFileSync(pngPath, png);
  const module = await import('png-to-ico');
  const pngToIco = module.default || module;
  const ico = await pngToIco(pngPath);
  fs.writeFileSync(path.join(root, 'build', 'icon.ico'), ico);
  console.log('Generated build/icon.ico');
})().catch((error) => { console.error(error); process.exit(1); });
