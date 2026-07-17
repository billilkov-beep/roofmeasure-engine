const fs = require('node:fs');
const path = require('node:path');

const sourcePath = path.join(__dirname, 'visual-installed.e2e.cjs');
const generatedPath = path.join(__dirname, '.visual-generated.cjs');
let source = fs.readFileSync(sourcePath, 'utf8');
source = source.replace(
  '    const page = await app.firstWindow();',
  "    const page = await app.firstWindow();\n    page.setDefaultTimeout(7000);\n    page.setDefaultNavigationTimeout(7000);"
);
source = source.replace(
  '    await app.close();',
  "    try {\n      await Promise.race([app.close(), new Promise((resolve) => setTimeout(resolve, 5000))]);\n    } finally {\n      const process = app.process();\n      if (process && process.exitCode == null) process.kill();\n    }"
);
fs.writeFileSync(generatedPath, source);
require(generatedPath);
