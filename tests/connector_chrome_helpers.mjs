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
      const root={id:'1'.repeat(32),name:'筆記',path:'D:/PCS/筆記',parent_id:null};
      const folder={id:'2'.repeat(32),name:'旅行',path:'D:/PCS/筆記/旅行',parent_id:root.id};
      const nested={id:'3'.repeat(32),name:'京都',path:'D:/PCS/筆記/旅行/京都',parent_id:folder.id};
      const external={id:'4'.repeat(32),name:'專案',path:'E:/專案',parent_id:null};
      globalThis.testNative={calls:[],instances:[{instance_id:'fixture-pcs',display_name:'PCS · 測試工作區',capabilities:['save_destination_v1']}],state:'committing',
        roots:[root,external],folders:[root,folder,nested,external],default_id:root.id,errors:{}};
      chrome.runtime.connectNative=()=>{let receive;return {
        onMessage:{addListener:f=>{receive=f;}},onDisconnect:{addListener(){}},disconnect(){},postMessage(message){
          testNative.calls.push(message);let result;
          let error=testNative.errors[message.command];
          if(message.command==='hello')result={instances:testNative.instances};
          else if(message.command==='destinations'){
            const current=testNative.folders.find(item=>item.id===(message.folder_id||testNative.default_id));
            if(current)result={roots:testNative.roots,current,folders:testNative.folders.filter(item=>item.parent_id===current.id),default_id:testNative.default_id};
            else error={code:'destination_changed',message:'資料夾已變更，請重新選擇。'};
          }
          else if(message.command==='status')result={state:testNative.state,...(testNative.state==='committed'?{title:'京都四天旅行安排',path:'D:/PCS/筆記/旅行/京都.md'}:{})};
          else if(message.command==='finish')result=testNative.finishResult||{state:testNative.finishState||'committing'};
          else if(message.command==='cancel')result={state:'cancelled'};
          else result={state:'receiving'};
          const reply=()=>receive(error?{request_id:message.request_id,ok:false,error}:{request_id:message.request_id,ok:true,result});
          if(message.command==='hello'&&testNative.holdHello)testNative.releaseHello=reply;
          else if(message.command==='begin'&&testNative.holdBegin)testNative.releaseBegin=reply;
          else if(message.command==='finish'&&testNative.holdFinish)testNative.releaseFinish=reply;
          else if(message.command==='destinations'&&testNative.holdDestinations)testNative.releaseDestinations=reply;
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
