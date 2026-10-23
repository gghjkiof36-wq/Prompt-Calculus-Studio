import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

export async function extensionFixture(body,{width=1100,height=900,waitForEntry=true}={}) {
  const {chromium}=await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const profile=await mkdtemp(path.join(tmpdir(),'pcs-extension-synthetic-'));
  const cleanup=async()=>{
    if(!path.resolve(profile).startsWith(path.resolve(tmpdir())+path.sep+'pcs-extension-synthetic-'))throw new Error('Unexpected test profile path');
    await rm(profile,{recursive:true,force:true});
  };
  const extension=fileURLToPath(new URL('../extensions/chrome/',import.meta.url));
  const context=await chromium.launchPersistentContext(profile,{headless:true,ignoreDefaultArgs:['--disable-extensions'],
    executablePath:process.env.PCS_EXTENSION_EXECUTABLE||process.env.PCS_CHROME_EXECUTABLE,
    viewport:{width,height},colorScheme:'dark',acceptDownloads:true,
    args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
  try{
    const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:10000});
    await worker.evaluate(()=>{
      globalThis.testNative={calls:[],instances:[{instance_id:'fixture-pcs',display_name:'PCS · 測試工作區'}],state:'previewing'};
      chrome.runtime.connectNative=()=>{let receive;return {
        onMessage:{addListener:f=>{receive=f;}},onDisconnect:{addListener(){}},disconnect(){},postMessage(message){
          testNative.calls.push(message);let result;
          if(message.command==='hello')result={instances:testNative.instances};
          else if(message.command==='status')result={state:testNative.state};
          else if(message.command==='finish')result={state:'previewing'};
          else if(message.command==='cancel')result={state:'cancelled'};
          else result={state:'receiving'};
          const reply=()=>receive({request_id:message.request_id,ok:true,result});
          if(message.command==='hello'&&testNative.holdHello)testNative.releaseHello=reply;
          else if(message.command==='finish'&&testNative.holdFinish)testNative.releaseFinish=reply;
          else queueMicrotask(reply);
        }};
      };
    });
    await context.route('**/*',route=>{
      const url=route.request().url();
      if(url.startsWith('https://chatgpt.com/'))return route.fulfill({contentType:'text/html;charset=utf-8',body});
      if(url.startsWith('chrome-extension://'))return route.continue();
      return route.abort();
    });
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('https://chatgpt.com/c/synthetic-kyoto');
    if(waitForEntry)await page.locator('[data-pcs-ui="entry"]').waitFor();
    const capture=()=>worker.evaluate(async()=>{
      const [tab]=await chrome.tabs.query({url:'https://chatgpt.com/*'});
      const [{result}]=await chrome.scripting.executeScript({target:{tabId:tab.id},func:()=>chrome.runtime.sendMessage({action:'capture'})});
      return {result:result.ok?result.result:{capture_error:result.error}};
    });
    return {context,page,worker,errors,capture,close:async()=>{await context.close();await cleanup();}};
  }catch(error){await context.close();await cleanup();throw error;}
}
