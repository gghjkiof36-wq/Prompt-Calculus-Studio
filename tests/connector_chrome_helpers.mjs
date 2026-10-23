import {mkdtemp,rm,readFile,mkdir,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import path from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';

export async function extensionFixture(body,{width=1100,height=900,waitForEntry=true}={}) {
  const {chromium}=await import(pathToFileURL(process.env.PCS_PLAYWRIGHT_MODULE).href);
  const profile=await mkdtemp(path.join(tmpdir(),'pcs-extension-synthetic-'));
  const downloadPath=path.join(profile,'downloads');await mkdir(downloadPath);
  await mkdir(path.join(profile,'Default'));
  await writeFile(path.join(profile,'Default','Preferences'),JSON.stringify({download:{default_directory:downloadPath,prompt_for_download:false,directory_upgrade:true},savefile:{default_directory:downloadPath}}));
  const cleanup=async()=>{
    if(!path.resolve(profile).startsWith(path.resolve(tmpdir())+path.sep+'pcs-extension-synthetic-'))throw new Error('Unexpected test profile path');
    await rm(profile,{recursive:true,force:true});
  };
  const extension=fileURLToPath(new URL('../extensions/chrome/',import.meta.url));
  const context=await chromium.launchPersistentContext(profile,{headless:true,ignoreDefaultArgs:['--disable-extensions'],
    executablePath:process.env.PCS_EXTENSION_EXECUTABLE||process.env.PCS_CHROME_EXECUTABLE,
    viewport:{width,height},colorScheme:'dark',acceptDownloads:true,downloadsPath:downloadPath,
    args:[`--disable-extensions-except=${extension}`,`--load-extension=${extension}`]});
  try{
    const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker',{timeout:10000});
    await worker.evaluate(()=>{
      globalThis.testDownloadActions=[];
      chrome.downloads.open=(id,callback)=>{testDownloadActions.push({action:'open',id});callback?.();};
      chrome.downloads.show=id=>{testDownloadActions.push({action:'reveal',id});};
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
    const cdp=await context.newCDPSession(page);await cdp.send('Browser.setDownloadBehavior',{behavior:'allow',downloadPath});await cdp.detach();
    const download=async action=>{
      const previous=await worker.evaluate(async()=>((await chrome.storage.local.get('downloads')).downloads||[]).map(item=>item.download_id));
      await action();
      const item=await worker.evaluate(previous=>new Promise((resolve,reject)=>{const timeout=setTimeout(()=>{clearInterval(timer);reject(new Error('Download did not complete'));},7000);const timer=setInterval(async()=>{const records=(await chrome.storage.local.get('downloads')).downloads||[];const item=records.find(item=>!previous.includes(item.download_id)&&item.state==='complete');if(item){clearInterval(timer);clearTimeout(timeout);resolve(item);}},20);}),previous);
      if(!path.resolve(item.path).startsWith(path.resolve(downloadPath)+path.sep))throw new Error(JSON.stringify({message:'Download escaped its isolated test directory',actual:item.path,expected:downloadPath}));
      return {...item,text:await readFile(item.path,'utf8')};
    };
    return {context,page,worker,errors,capture,download,close:async()=>{await context.close();await cleanup();}};
  }catch(error){await context.close();await cleanup();throw error;}
}
