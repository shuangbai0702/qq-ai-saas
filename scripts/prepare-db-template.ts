import path from 'node:path';import {preparePGliteTemplate} from '../packages/database/template.js';
const result=await preparePGliteTemplate(path.resolve(process.argv[2]||'resources/pglite-template'));
console.log(JSON.stringify({prepared:true,...result,note:'Empty deployment artifact only; no production data read. Ship resources/pglite-template with the same PGlite version.'}));
