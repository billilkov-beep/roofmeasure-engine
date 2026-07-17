const fs = require('node:fs');
const path = require('node:path');

const target = path.join(__dirname, 'patch-base.cjs');
let source = fs.readFileSync(target, 'utf8');
source = source.replace(
  '  requireFile(BASE_INSTALL, 1);',
  "  if (!fs.existsSync(BASE_INSTALL) || !fs.statSync(BASE_INSTALL).isDirectory()) throw new Error(`Original visual runtime directory is missing: ${BASE_INSTALL}`);"
);
source = source.replace(
  "  index = index.replace('</body>', '<script src=\"manual-693.js\"></script><script src=\"release-693.js\"></script></body>');",
  `  const releaseScripts = '<script src="manual-693.js"></script><script src="release-693.js"></script>';
  index = index.replace(/<\\/body>/i, releaseScripts + '</body>');
  if (!index.includes('manual-693.js') || !index.includes('release-693.js')) index = index.replace(/<\\/html>/i, releaseScripts + '</html>');
  if (!index.includes('manual-693.js') || !index.includes('release-693.js')) throw new Error('Manual and responsive release scripts were not inserted into the original Player HTML.');`
);
source = source.replace(
  "  if (!index.includes('../assets/brand/goldslots-logo.svg')) throw new Error('The original Gold Slots logo reference was not preserved.');",
  "  if (!index.includes('../assets/brand/goldslots-logo.svg')) throw new Error('The original Gold Slots logo reference was not preserved.');\n  if (!index.includes('manual-693.js') || !index.includes('release-693.js')) throw new Error('The manual release scripts are missing from the final Player HTML.');"
);
fs.writeFileSync(target, source);
require(target);
