import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import test from 'node:test';

test('installed frontend 1.53.6 registration preserves native parameter capability and rejects later replacements', {
    skip: !process.env.PCS_FRONTEND_ASSETS && 'Set PCS_FRONTEND_ASSETS to an existing frontend 1.53.6 static/assets directory',
}, () => {
    const runner = fileURLToPath(new URL('./fixtures/frontend_1536_cases.mjs', import.meta.url));
    const result = JSON.parse(execFileSync(process.execPath, [runner], { encoding: 'utf8', env: process.env, timeout: 30000 }));
    assert.equal(result.mode, 'fixed');
    assert.equal(result.frontend, '1.53.6');
    assert.equal(result.cases.length, 14);
});
