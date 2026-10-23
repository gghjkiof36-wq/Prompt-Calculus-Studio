import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Script} from 'node:vm';
import {bundleContent, outputPath} from '../tools/build_chrome_content.mjs';

test('classic content bundle exactly matches its pinned source build', async () => {
  const first = await bundleContent(), second = await bundleContent();
  assert.equal(first.code, second.code);
  assert.equal(await readFile(outputPath, 'utf8'), first.code);
  assert.deepEqual(first.inputs, ['extensions/chrome/core.mjs', 'extensions/chrome/dom.mjs',
    'extensions/chrome/entry.js', 'extensions/chrome/inpage.mjs']);
  assert.doesNotThrow(() => new Script(first.code));
  assert.doesNotMatch(first.code, /\bimport\s*\(/);
});

test('manifest loads only the classic isolated script without exposing page resources', async () => {
  const manifest = JSON.parse(await readFile(new URL('../extensions/chrome/manifest.json', import.meta.url), 'utf8'));
  assert.deepEqual(manifest.content_scripts[0].js, ['content.js']);
  assert.equal(manifest.content_scripts[0].world || 'ISOLATED', 'ISOLATED');
  assert.equal(manifest.web_accessible_resources, undefined);
  assert.deepEqual(manifest.permissions, ['activeTab', 'scripting', 'storage', 'nativeMessaging', 'downloads', 'downloads.open']);
});
