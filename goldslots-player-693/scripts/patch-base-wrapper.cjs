const fs = require('node:fs');
const path = require('node:path');

const target = path.join(__dirname, 'patch-base.cjs');
let source = fs.readFileSync(target, 'utf8');
source = source.replace(
  '  requireFile(BASE_INSTALL, 1);',
  "  if (!fs.existsSync(BASE_INSTALL) || !fs.statSync(BASE_INSTALL).isDirectory()) throw new Error(`Original visual runtime directory is missing: ${BASE_INSTALL}`);"
);
fs.writeFileSync(target, source);
require(target);
