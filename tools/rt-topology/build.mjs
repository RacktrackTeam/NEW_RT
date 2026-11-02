// Builds RT-EOS's Topology tab into mobile/rt/ for the Elevation tab:   node tools/rt-topology/build.mjs
// React, three and the rest come from RT-EOS's own client/node_modules, so the result is exactly RT-EOS's drawing.
import { createRequire } from 'node:module';
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
const RT = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../rt_eos/client');   // the RT-EOS client copied into this folder (its node_modules link into _cache)
const require = createRequire(path.join(RT, 'package.json'));
const esbuild = require('esbuild');
const here = path.dirname(new URL(import.meta.url).pathname), out = path.resolve(here, '../../mobile/rt');
await esbuild.build({
  entryPoints: [path.join(here, 'entry.jsx')], bundle: true, minify: true, format: 'iife', target: 'es2019', outfile: path.join(out, 'rt-topology.js'),
  jsx: 'automatic', loader: { '.module.css': 'local-css', '.css': 'css', '.woff2': 'empty', '.woff': 'empty', '.png': 'dataurl', '.svg': 'dataurl', '.jpg': 'dataurl' },
  alias: { rteos: path.join(RT, 'src') }, nodePaths: [path.join(RT, 'node_modules')], define: { 'process.env.NODE_ENV': '"production"', 'import.meta.env.DEV': 'false', 'import.meta.env.PROD': 'true', 'import.meta.env.MODE': '"production"' },
  logLevel: 'warning',
});
// RT-EOS's design tokens, scoped to the mount point so they cannot change the rest of the app
const g = readFileSync(path.join(RT, 'src/styles/global.css'), 'utf8'), root = g.match(/:root\s*\{[\s\S]*?\n\}/)[0].replace(':root', '.rteos');
writeFileSync(path.join(out, 'rt-topology.css'), root + '\n.rteos { font-family: var(--font); color: var(--ink); }\n' + readFileSync(path.join(out, 'rt-topology.css'), 'utf8'));
console.log('built', out);
