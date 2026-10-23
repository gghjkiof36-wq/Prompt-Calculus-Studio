// Chrome content scripts run in an isolated world. No page script bridge is exposed.
if (!globalThis.__pcsPageEntry) {
  globalThis.__pcsPageEntry = import(chrome.runtime.getURL('inpage.mjs'))
    .then(module => module.installInPage())
    .catch(error => { delete globalThis.__pcsPageEntry; console.error('PCS entry:', error); });
}
