// Source entry for content.js. Chrome loads the generated classic script in its
// isolated world; no runtime module fetch or page script bridge is needed.
import {installInPage} from './inpage.mjs';

if (!globalThis.__pcsPageEntry) globalThis.__pcsPageEntry = installInPage();
