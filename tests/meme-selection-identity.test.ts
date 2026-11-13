import test from 'node:test';
import assert from 'node:assert/strict';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
test('current meme definition identity, favorite defaults and reminder cadence',()=>{
 const out=execFileSync(path.resolve(`references/astrbot/.venv/${process.platform==='win32'?'Scripts/python.exe':'bin/python'}`),['-B','tests/meme_selection_identity_test.py'],{encoding:'utf8',windowsHide:true});
 assert.ok(out!==undefined);
});
