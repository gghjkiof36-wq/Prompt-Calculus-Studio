import {selectCapture,statusLabel,FINAL_STATES} from './core.mjs';
import {locateConversation,messageCopyControl,conversationHeaderSlot} from './dom.mjs';

let controller;
const CSS=`:host{all:initial;font:14px/1.45 system-ui,'Segoe UI','Microsoft JhengHei',sans-serif;--bg:#2f2f2f;--text:#ececec;--muted:#aaa;--line:#ffffff24;--hover:#ffffff12;color:var(--text);color-scheme:dark}:host([data-theme=light]){--bg:#fff;--text:#292929;--muted:#666;--line:#0002;--hover:#0000000a;color-scheme:light}*{box-sizing:border-box}[hidden]{display:none!important}button,select{font:inherit;color:inherit;cursor:pointer}button{background:transparent;border:0;border-radius:6px;min-height:32px;padding:5px 8px;text-align:left}button:hover{background:var(--hover)}button:disabled{opacity:.4;cursor:default}button:focus-visible,select:focus-visible{outline:2px solid var(--muted);outline-offset:2px}.entry{color:var(--muted);font-size:13px;padding:4px 7px}.entry[aria-expanded=true]{background:var(--hover);color:var(--text)}.menu{width:248px;max-width:calc(100vw - 20px);padding:6px;background:var(--bg);border:1px solid var(--line);border-radius:12px;box-shadow:0 4px 16px #0003;max-height:calc(100vh - 24px);overflow:auto}.menu>button{display:block;width:100%;padding:8px 10px}.title{font-size:12px;color:var(--muted);padding:5px 10px}.section{height:1px;background:var(--line);margin:5px 4px}.tools{display:flex;justify-content:space-between;padding:0 3px}.tools button{font-size:12px;color:var(--muted)}.status{padding:8px 10px;font-size:13px}.receipt{padding:5px 7px}.receipt span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.receipt button{font-size:12px;color:var(--muted)}select{width:calc(100% - 20px);margin:4px 10px 8px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg)}.submenu-trigger{display:flex!important;align-items:center;justify-content:space-between}.submenu-trigger:after,.folder-arrow:after{content:'';display:block;width:6px;height:6px;border-top:1.5px solid currentColor;border-right:1.5px solid currentColor;transform:rotate(45deg);margin:0 3px}.folder-row{display:flex;align-items:stretch}.folder-row>.folder-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;padding:8px 10px}.folder-row>.folder-arrow{flex:0 0 32px;display:flex;align-items:center;justify-content:center;padding:6px}.cascade{width:240px;max-height:calc(100vh - 20px);overflow-y:auto;overscroll-behavior:contain}.cascade>button{display:flex;align-items:center;justify-content:space-between}.check{display:flex;align-items:center;justify-content:center;width:32px;height:32px;cursor:pointer}.check input{appearance:none;width:20px;height:20px;margin:0;border:1px solid var(--muted);border-radius:4px;background:transparent;cursor:pointer;position:relative}.check input:checked{background:var(--text);border-color:var(--text)}.check input:checked:after{content:'';position:absolute;left:6px;top:2px;width:5px;height:10px;border:solid var(--bg);border-width:0 2px 2px 0;transform:rotate(45deg)}.check input:focus-visible{outline:2px solid var(--text);outline-offset:3px}.check input:disabled{opacity:.45;cursor:default}`;

function host(kind){const element=document.createElement('span');element.dataset.pcsUi=kind;const root=element.attachShadow({mode:'open'});const style=document.createElement('style');style.textContent=CSS+':host([hidden]){display:none!important}.receipt{display:flex;align-items:center;gap:4px}.receipt span{flex:1;min-width:0}.receipt button{display:flex;align-items:center;justify-content:center;flex:0 0 32px;width:32px;height:32px;padding:7px}.receipt svg{width:18px;height:18px;pointer-events:none}';root.append(style);return {element,root};}
function button(text,action,className=''){const result=document.createElement('button');result.type='button';result.textContent=text;result.className=className;result.addEventListener('click',e=>{if(e.isTrusted)action();});return result;}
function recordButton(label,action,kind){const result=button('',action);result.title=label;result.setAttribute('aria-label',label);const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.setAttribute('viewBox','0 0 24 24');svg.setAttribute('aria-hidden','true');svg.setAttribute('fill','none');svg.setAttribute('stroke','currentColor');svg.setAttribute('stroke-width','1.6');svg.setAttribute('stroke-linecap','round');svg.setAttribute('stroke-linejoin','round');const path=document.createElementNS(svg.namespaceURI,'path');path.setAttribute('d',kind==='file'?'M14 3h7v7 M21 3l-10 10 M10 5H3v16h16v-7':'M3 7V5h7l2 2h9v13H3z M3 10h18');svg.append(path);result.append(svg);return result;}
function visible(element){for(let node=element;node;node=node.parentElement){const s=getComputedStyle(node);if(node.hidden||node.getAttribute('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden')return false;}return true;}
function messageElements(){try{return locateConversation().messages.map(item=>item.element);}catch{return [];}}

export function installInPage(){
  if(controller)return controller;
  if(!chrome?.runtime?.id)throw new Error('PCS extension context unavailable');
  let active=false,snapshot=null,selected=new Set(),checks=[],pending=null,busy=false,notice='',poll=null;
  let instances=[],recents=[],lastUrl=location.href,refreshTimer=null,reading=false,refreshAgain=false,generation=0,historyTimer=null;
  let destination=null,destinationRequest=0,hoverTimer=null,closeTimer=null,saveDeadline=0,saveTimer=null,actionSequence=0;
  const statusRequests=new Set();
  const submenuOpeners=new WeakMap();
  let menuOpen=false,menuMode='choices',menuAnchor=null,sourceAnchor=null;
  let selectionHandlers=null,selectionStyle=null,press=null,clickTimer=null,lastClick=null;
  const selectionToken=chrome.runtime.id;
  const messageButtons=new Map(),entry=host('entry'),popup=host('menu');
  entry.element.style.cssText='display:inline-flex;vertical-align:middle;align-self:center;flex-shrink:0;margin-inline:2px;pointer-events:auto;';
  popup.element.style.cssText='position:fixed;z-index:2147483000;';
  const headerButton=button('PCS',openHeader,'entry');headerButton.setAttribute('aria-haspopup','dialog');entry.root.append(headerButton);
  const menu=document.createElement('div');menu.className='menu';menu.setAttribute('role','dialog');menu.setAttribute('aria-label','PCS');popup.root.append(menu);

  async function request(action,fields={}){let response,timer;try{response=await Promise.race([chrome.runtime.sendMessage({action,...fields}),new Promise((_,reject)=>{timer=setTimeout(()=>reject(Object.assign(new Error('暫時沒有回應，請再試一次。'),{code:'timeout'})),action==='transfer'?120000:25000);})]);}catch(error){if(error.code==='timeout')throw error;throw Object.assign(new Error('擴充已更新，請重新整理頁面。'),{code:'extension_unavailable',cause:error});}finally{clearTimeout(timer);}if(!response?.ok)throw Object.assign(new Error(response?.error?.message||'操作失敗，請再試一次。'),{code:response?.error?.code||'failed'});return response.result;}
  function theme(){const h=document.documentElement,scheme=getComputedStyle(h).colorScheme;return h.classList.contains('dark')?'dark':h.classList.contains('light')?'light':h.dataset.theme==='dark'?'dark':h.dataset.theme==='light'?'light':scheme==='dark'?'dark':scheme==='light'?'light':matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light';}
  function mount(){
    const slot=conversationHeaderSlot();
    entry.element.hidden=!slot;
    if(slot&&(entry.element.parentElement!==slot.parent||entry.element.nextElementSibling!==slot.before))slot.parent.insertBefore(entry.element,slot.before);
    if(!popup.element.isConnected)document.body.append(popup.element);
    for(const [anchor,item] of messageButtons){if(!anchor.isConnected||!item.element.isConnected){item.element.remove();messageButtons.delete(anchor);}}
    const elements=messageElements();
    for(const anchor of elements){
      if(messageButtons.has(anchor))continue;
      const copy=messageCopyControl(anchor,elements);
      if(!copy)continue;
      const item=host('message-action');item.element.style.cssText='display:inline-flex;vertical-align:middle;';
      const action=button('PCS',()=>openMessage(anchor,item.element),'entry');action.setAttribute('aria-label','PCS：此則訊息');action.setAttribute('aria-haspopup','dialog');item.root.append(action);copy.after(item.element);messageButtons.set(anchor,item);
    }
    const value=theme();for(const item of [entry.element,popup.element,...checks.map(c=>c.host),...(destination?.levels.map(level=>level.host.element)||[]),...[...messageButtons.values()].map(c=>c.element)])item.dataset.theme=value;
    positionMenu();positionSubmenus();
  }
  function positionMenu(){if(!menuOpen||!menuAnchor?.isConnected)return;popup.element.hidden=menuAnchor===entry.element&&entry.element.hidden;if(popup.element.hidden)return;const rect=menuAnchor.getBoundingClientRect(),height=menu.getBoundingClientRect().height,width=menu.getBoundingClientRect().width;popup.element.style.left=`${Math.max(10,Math.min(innerWidth-width-10,rect.right-width))}px`;popup.element.style.top=`${Math.max(10,rect.bottom+6+height>innerHeight?rect.top-height-6:rect.bottom+6)}px`;}
  function separator(){const line=document.createElement('div');line.className='section';menu.append(line);}
  function caption(text){const node=document.createElement('div');node.className='title';node.textContent=text;menu.append(node);}
  function allButton(action){const item=button('全選',action);item.title='選取頁面已載入的訊息';item.setAttribute('aria-description',item.title);return item;}
  function operationLocked(){return !!pending&&!FINAL_STATES.has(pending.state);}
  function clearOutcome(){if(pending&&FINAL_STATES.has(pending.state)){clearTimeout(poll);clearTimeout(saveTimer);pending=null;notice='';}}
  function render(){
    const focused=popup.root.activeElement,focusLabel=focused?.getAttribute('aria-label')||focused?.textContent;
    const usableSelection=!pending||(!operationLocked()&&snapshot?.context.url===location.href&&selected.size>0);
    headerButton.textContent=active&&selected.size?`PCS ${selected.size}`:'PCS';headerButton.setAttribute('aria-expanded',String(menuOpen&&menuAnchor===entry.element));
    popup.element.hidden=!menuOpen||(menuAnchor===entry.element&&entry.element.hidden);menu.replaceChildren();syncChecks();for(const item of checks)item.input.disabled=busy||operationLocked()||!!destination;for(const item of messageButtons.values())item.root.querySelector('button').disabled=busy||!!destination||operationLocked();
    if(menuMode==='recent'){
      menu.append(button('‹ 返回',()=>{menuMode=active||snapshot?'selection':'choices';render();}));caption('下載紀錄');const saved=recents.filter(r=>r.state==='committed'||r.kind==='download'||!FINAL_STATES.has(r.state));if(!saved.length)caption('尚無紀錄');
      for(const receipt of saved){const row=document.createElement('div');row.className='receipt';const name=document.createElement('span');name.textContent=receipt.title;name.title=receipt.path||receipt.title;
        if(receipt.kind==='download'&&(receipt.state!=='complete'||receipt.exists===false)){const detail=document.createElement('small');detail.style.cssText='display:block;color:var(--muted)';detail.textContent=receipt.exists===false?'檔案已移除':receipt.state==='in_progress'?'下載中…':receipt.error==='USER_CANCELED'?'已取消':'下載失敗';name.append(detail);}
        if(receipt.kind!=='download'&&!FINAL_STATES.has(receipt.state)){const detail=document.createElement('small');detail.style.cssText='display:block;color:var(--muted)';detail.textContent='尚未確認保存結果';name.append(detail);}
        const folder=recordButton('開啟資料夾',()=>receiptAction('reveal',receipt),'folder'),open=recordButton('開啟文件',()=>receiptAction('open',receipt),'file');folder.disabled=open.disabled=receipt.kind==='download'?(receipt.state!=='complete'||receipt.exists===false):receipt.state!=='committed';row.append(name,folder,open);if(receipt.kind!=='download'&&!FINAL_STATES.has(receipt.state))row.append(button('查詢結果',()=>receiptAction('status',receipt)));menu.append(row);}
      if(notice){const text=document.createElement('div');text.className='status';text.textContent=notice;menu.append(text);}
    }else if(menuMode==='choices'&&!pending){
      menu.append(allButton(()=>startSelection(true)),button('選取訊息',()=>startSelection(false)));
      if(notice){const text=document.createElement('div');text.className='status';text.textContent=notice;menu.append(text);}if(menuAnchor===entry.element){separator();menu.append(button('下載紀錄',showRecents));}
    }else{
      const loaded=selected.size>0&&selected.size===snapshot?.capture.messages.length&&snapshot.capture.scope==='loaded';
      caption(pending&&(operationLocked()||!usableSelection)?(pending.source_title||pending.title||'傳送結果'):menuMode==='message'?'此則訊息':`已選 ${selected.size} 則${loaded?'（已載入）':''}`);
      if(snapshot?.capture.messages.some(message=>selected.has(message.id)&&message.resources?.some(resource=>['chart','image','embed','attachment'].includes(resource.kind))))caption('部分圖表或附件僅保留來源連結');
      if(active&&usableSelection){const tools=document.createElement('div');tools.className='tools';const all=allButton(()=>{clearOutcome();selected=new Set(snapshot?.capture.messages.map(m=>m.id)||[]);syncChecks();render();});all.disabled=busy||!!destination;const cancelChoice=button('取消',cancelSelection);tools.append(all,cancelChoice);menu.append(tools);}
      const send=button('儲存到 PCS',()=>chooseDestination(true),'submenu-trigger');send.disabled=!selected.size||busy||operationLocked();send.setAttribute('aria-haspopup','menu');send.setAttribute('aria-expanded',String(!!destination));hoverOpen(send,()=>chooseDestination(false));submenuOpeners.set(send,()=>chooseDestination(false,true));menu.append(send);if(destination){destination.anchor=send;if(destination.levels[0])destination.levels[0].anchor=send;}
      const json=button('下載 JSON',()=>{closeDestination();downloadSelected('json');}),md=button('下載 Markdown',()=>{closeDestination();downloadSelected('md');});json.disabled=md.disabled=!selected.size||!snapshot||snapshot.context.url!==location.href||busy;menu.append(json,md);
      if(notice||pending&&pending.state!=='committed'){separator();const text=document.createElement('div');text.className='status';text.setAttribute('aria-live','polite');text.textContent=notice||(pending?(pending.state==='failed'&&pending.message||statusLabel(pending.state)):'');menu.append(text);}
      if(pending){if(!FINAL_STATES.has(pending.state)){if(pending.state==='unknown')menu.append(button('查詢結果',()=>checkStatus(true)));menu.append(button('取消',cancelSelection));}if(FINAL_STATES.has(pending.state)&&!usableSelection)menu.append(button('重新選取',newSelection));}
      else if(busy&&!active)menu.append(button('取消',cancelSelection));
      else if(notice&&!busy)menu.append(button('重新讀取',retrySelection));if(menuAnchor===entry.element){separator();menu.append(button('下載紀錄',showRecents));}
    }requestAnimationFrame(()=>{positionMenu();if(menuOpen&&focusLabel){const replacement=[...menu.querySelectorAll('button,select')].find(item=>(item.getAttribute('aria-label')||item.textContent)===focusLabel&&!item.disabled);(replacement||menu.querySelector('button:not(:disabled),select'))?.focus({preventScroll:true});}});
  }
  function focusMenu(){requestAnimationFrame(()=>{if(menuOpen)menu.querySelector('button:not(:disabled),select')?.focus({preventScroll:true});});}
  async function openHeader(){closeDestination();if(menuOpen&&menuAnchor===entry.element){menuOpen=false;render();return;}menuAnchor=entry.element;menuMode=pending||active||(snapshot&&!sourceAnchor)?'selection':'choices';menuOpen=true;notice='';await loadRecents();render();focusMenu();}
  async function openMessage(anchor,buttonHost){if(busy||destination||(pending&&!FINAL_STATES.has(pending.state)))return;pending=null;clearTimeout(poll);generation++;const expected=generation,url=location.href,action=++actionSequence;active=false;selected.clear();snapshot=null;removeChecks();sourceAnchor=anchor;menuAnchor=buttonHost;menuMode='message';menuOpen=true;busy=true;notice='';render();try{const fresh=await request('capture');if(generation!==expected||location.href!==url||fresh.context.url!==url)return;const index=messageElements().indexOf(anchor),id=fresh.anchors.find(a=>a.index===index)?.id;if(!id)throw new Error('訊息已更新，請再選一次。');snapshot=fresh;selected=new Set([id]);}catch(error){if(generation===expected)notice=error.message;}finally{if(action===actionSequence){busy=false;render();focusMenu();}}}
  async function startSelection(all){generation++;active=true;sourceAnchor=null;menuAnchor=entry.element;selected.clear();snapshot=null;menuMode='selection';notice='';await refresh(true);if(all&&snapshot)selected=new Set(snapshot.capture.messages.map(m=>m.id));syncChecks();render();if(!all&&snapshot){menuOpen=false;render();}}
  function cancelSelection(){const operation=operationLocked()?{operation_id:pending.operation_id,instance_id:pending.instance_id}:null;closeDestination();clearTimeout(poll);clearTimeout(saveTimer);generation++;actionSequence++;busy=false;active=false;pending=null;selected.clear();snapshot=null;sourceAnchor=null;removeChecks();menuMode='choices';notice='';render();focusMenu();if(operation){request('dismiss',operation).catch(()=>{});request('cancel',operation).then(()=>loadRecents(),()=>loadRecents());}}
  async function retrySelection(){if(menuMode==='message'&&sourceAnchor?.isConnected)return openMessage(sourceAnchor,menuAnchor);if(active)return refresh(true);return startSelection(false);}
  function restoreAttribute(element,name,value){if(value===null)element.removeAttribute(name);else element.setAttribute(name,value);}
  function syncChecks(){for(const item of checks){const checked=selected.has(item.id);item.input.checked=checked;if(checked)item.anchor.setAttribute('data-pcs-selected',selectionToken);else restoreAttribute(item.anchor,'data-pcs-selected',item.originalSelected);}}
  function cancelClick(){clearTimeout(clickTimer);clickTimer=null;press=null;}
  function removeChecks(){cancelClick();lastClick=null;selectionHandlers?.abort();selectionHandlers=null;selectionStyle?.remove();selectionStyle=null;for(const item of checks){item.host.remove();restoreAttribute(item.anchor,'data-pcs-selectable',item.originalSelectable);restoreAttribute(item.anchor,'data-pcs-selected',item.originalSelected);}checks=[];}
  function positionChecks(){
    const headerBottom=entry.element.isConnected&&!entry.element.hidden?entry.element.getBoundingClientRect().bottom+8:6;
    for(const item of checks){
      const rect=item.anchor.getBoundingClientRect(),gutter=rect.right+36<=innerWidth;
      item.host.hidden=!active||rect.bottom<headerBottom||rect.top>innerHeight||!item.anchor.isConnected||!visible(item.anchor);
      item.host.style.left=`${gutter?rect.right+4:Math.max(2,Math.min(rect.right-32,innerWidth-36))}px`;
      item.host.style.top=`${gutter?Math.min(Math.max(headerBottom,rect.top),Math.max(headerBottom,rect.bottom-32)):Math.max(headerBottom,rect.top-34)}px`;
    }
  }
  function toggleItem(item){if(!active||busy||operationLocked()||destination||!checks.includes(item))return;clearOutcome();if(selected.has(item.id))selected.delete(item.id);else selected.add(item.id);notice='';syncChecks();render();}
  function selectionItem(event){
    if(!active||busy||operationLocked()||destination||!event.isTrusted||event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return null;
    const interactive='a,button,input,textarea,select,option,label,summary,[contenteditable]:not([contenteditable="false"]),[role="button"],[role="link"],[role="menuitem"],[role="checkbox"],[role="radio"],[role="switch"],[role="slider"],[role="tab"],[role="textbox"],[tabindex]:not([tabindex="-1"]),[draggable="true"],canvas,svg,iframe,audio,video,[data-pcs-ui]';
    if(event.composedPath().some(node=>node instanceof Element&&node.matches(interactive)))return null;
    return checks.find(item=>item.anchor===event.target||item.anchor.contains(event.target))||null;
  }
  function bindSelection(){
    selectionStyle=document.createElement('style');selectionStyle.dataset.pcsUi='selection-style';
    selectionStyle.textContent=`[data-pcs-selectable="${selectionToken}"]{cursor:pointer}[data-pcs-selected="${selectionToken}"]{background-color:color-mix(in srgb,currentColor 8%,transparent);border-radius:8px}`;
    document.head.append(selectionStyle);selectionHandlers=new AbortController();const options={signal:selectionHandlers.signal};
    document.addEventListener('pointerdown',event=>{cancelClick();const item=event.button===0&&event.isPrimary?selectionItem(event):null;if(item)press={item,x:event.clientX,y:event.clientY,pointerId:event.pointerId,moved:false};},options);
    document.addEventListener('pointermove',event=>{if(press&&event.pointerId===press.pointerId&&Math.hypot(event.clientX-press.x,event.clientY-press.y)>5)press.moved=true;},options);
    document.addEventListener('pointercancel',cancelClick,options);
    document.addEventListener('selectionchange',()=>{if(!getSelection()?.isCollapsed)cancelClick();},options);
    document.addEventListener('click',event=>{
      const item=selectionItem(event),candidate=press;press=null;
      if(event.detail===1)lastClick=null;
      if(event.detail>1){cancelClick();if(lastClick?.item===item&&lastClick.generation===generation){if(lastClick.wasSelected)selected.add(item.id);else selected.delete(item.id);lastClick=null;syncChecks();render();}return;}
      if(!item||candidate?.item!==item||candidate.moved||!getSelection()?.isCollapsed)return;
      const expected=generation;clickTimer=setTimeout(()=>{clickTimer=null;if(generation!==expected||!getSelection()?.isCollapsed||!active||busy||operationLocked()||destination)return;lastClick={item,generation,wasSelected:selected.has(item.id)};toggleItem(item);},250);
    },options);
  }
  function buildChecks(){
    removeChecks();if(!active||!snapshot)return;const elements=messageElements();
    for(const item of snapshot.anchors||[]){
      const anchor=elements[item.index];if(!anchor)continue;const marker=host('selection');marker.element.style.cssText='position:fixed;z-index:2147482999;';
      const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';input.setAttribute('aria-label',`選取第 ${item.index+1} 則訊息`);input.checked=selected.has(item.id);
      input.addEventListener('change',e=>{if(!e.isTrusted)return;cancelClick();lastClick=null;if(operationLocked()||busy||destination){input.checked=selected.has(item.id);return;}toggleItem(checks.find(check=>check.input===input));});
      label.append(input);marker.root.append(label);document.body.append(marker.element);
      const originalSelectable=anchor.getAttribute('data-pcs-selectable'),originalSelected=anchor.getAttribute('data-pcs-selected');anchor.setAttribute('data-pcs-selectable',selectionToken);
      checks.push({id:item.id,anchor,input,host:marker.element,originalSelectable,originalSelected});
    }
    bindSelection();syncChecks();mount();positionChecks();
  }
  async function refresh(explicit=false){if(!active||operationLocked()||destination)return;if(reading){refreshAgain=true;return;}reading=true;const expected=generation,url=location.href;try{const fresh=await request('capture');if(!active||operationLocked()||destination||generation!==expected||location.href!==url||fresh.context.url!==url)return;const changed=snapshot&&(fresh.fingerprint!==snapshot.fingerprint||fresh.context.url!==snapshot.context.url||fresh.context.tabId!==snapshot.context.tabId);if(changed){clearOutcome();selected.clear();notice='對話已更新，請重新選取。';}else if(explicit){clearOutcome();notice='';}snapshot=fresh;buildChecks();}catch(error){if(generation===expected&&active){snapshot=null;selected.clear();removeChecks();notice=error.message;menuOpen=true;}}finally{reading=false;render();if(refreshAgain){refreshAgain=false;scheduleRefresh();}}}
  async function downloadSelected(ext){if(busy||!snapshot)return;const expected=generation,url=location.href,context=snapshot.context,capture=selectCapture(snapshot.capture,selected),action=++actionSequence;busy=true;notice='';render();try{await request('download',{format:ext,context,capture});}catch(error){if(generation===expected&&location.href===url){if(error.code==='page_changed'){selected.clear();snapshot=null;removeChecks();}notice=error.message;}}finally{if(action===actionSequence){busy=false;render();}}}
  function selectionValid(intent){return generation===intent.generation&&location.href===intent.url&&snapshot?.context.fingerprint===intent.context.fingerprint;}
  function destinationValid(view){return !!view?.intent&&destination===view&&selectionValid(view.intent);}
  function hoverOpen(element,action){
    element.addEventListener('pointerenter',event=>{if(!event.isTrusted)return;clearTimeout(hoverTimer);clearTimeout(closeTimer);hoverTimer=setTimeout(action,140);});
    element.addEventListener('pointerleave',()=>clearTimeout(hoverTimer));
  }
  function trimLevels(view,index){
    for(const level of view.levels.splice(index)){level.anchor?.setAttribute('aria-expanded','false');level.host.element.remove();}
  }
  function closeDestination(){
    clearTimeout(hoverTimer);clearTimeout(closeTimer);hoverTimer=closeTimer=null;
    if(destination){trimLevels(destination,0);destination.anchor?.setAttribute('aria-expanded','false');destination=null;destinationRequest++;}
    for(const check of checks)check.input.disabled=busy||operationLocked();
    for(const tool of menu.querySelectorAll('.tools button'))tool.disabled=tool.textContent!=='取消'&&(busy||operationLocked());
    for(const item of messageButtons.values())item.root.querySelector('button').disabled=busy||!!(pending&&!FINAL_STATES.has(pending.state));
  }
  function positionSubmenus(){
    if(!destination)return;
    for(const level of destination.levels){
      if(!level.anchor?.isConnected){closeDestination();return;}
      const anchor=level.anchor.getBoundingClientRect(),parent=level.anchor.closest('.menu')?.getBoundingClientRect()||anchor,rect=level.menu.getBoundingClientRect();
      const right=anchor.right+4,left=anchor.left-rect.width-4;
      const x=Math.max(8,Math.min(innerWidth-rect.width-8,right+rect.width<=innerWidth-8?right:left));
      level.back.hidden=!(x<parent.right&&x+rect.width>parent.left);
      level.host.element.style.left=`${x}px`;
      level.host.element.style.top=`${Math.max(8,Math.min(innerHeight-level.menu.getBoundingClientRect().height-8,anchor.top))}px`;
    }
  }
  function makeLevel(view,index,anchor){
    trimLevels(view,index);const item=host('submenu');item.element.style.cssText=`position:fixed;z-index:${2147483001+index};`;
    item.element.dataset.theme=theme();const contents=document.createElement('div');contents.className='menu cascade';contents.setAttribute('role','menu');contents.setAttribute('aria-label','儲存位置');item.root.append(contents);document.body.append(item.element);
    const level={host:item,menu:contents,anchor,serial:++destinationRequest,instance_id:'',loading:true};
    level.back=button('返回',()=>{const parentAnchor=level.anchor;trimLevels(view,view.levels.indexOf(level));if(!view.levels.length)closeDestination();parentAnchor?.focus({preventScroll:true});});level.back.setAttribute('role','menuitem');level.back.hidden=true;contents.append(level.back);contents.addEventListener('pointerenter',()=>clearTimeout(hoverTimer));view.levels.push(level);anchor?.setAttribute('aria-expanded','true');positionSubmenus();return level;
  }
  function levelValid(view,level){return destinationValid(view)&&view.levels.includes(level);}
  function levelText(level,text){const node=document.createElement('div');node.className='status';node.setAttribute('aria-live','polite');node.textContent=text;level.menu.append(node);}
  function levelButton(level,label,action,className=''){const item=button(label,action,className);item.setAttribute('role','menuitem');level.menu.append(item);return item;}
  function levelRule(level){const line=document.createElement('div');line.className='section';level.menu.append(line);}
  function focusLevel(level){requestAnimationFrame(()=>{if(destination?.levels.includes(level))level.menu.querySelector('button:not(:disabled):not([hidden])')?.focus({preventScroll:true});});}
  function folderRow(view,level,folder){
    const index=view.levels.indexOf(level),row=document.createElement('div');row.className='folder-row';
    const name=button(folder.name,()=>saveToDestination(view,level.instance_id,folder.id),'folder-name');name.title=folder.path;name.setAttribute('role','menuitem');
    const expand=button('',()=>browseFolder(view,index+1,expand,level.instance_id,folder.id,true),'folder-arrow');expand.setAttribute('role','menuitem');expand.setAttribute('aria-label',`展開 ${folder.name}`);expand.setAttribute('aria-haspopup','menu');expand.setAttribute('aria-expanded','false');
    const open=keyboard=>browseFolder(view,index+1,expand,level.instance_id,folder.id,keyboard);
    hoverOpen(expand,()=>open(false));submenuOpeners.set(name,()=>open(true));submenuOpeners.set(expand,()=>open(true));
    row.addEventListener('pointerenter',event=>{if(event.isTrusted&&!row.contains(view.levels[index+1]?.anchor))trimLevels(view,index+1);});
    row.append(name,expand);level.menu.append(row);
  }
  function showRoots(view,level,anchor,keyboard){
    if(!destinationValid(view))return;
    const index=view.levels.indexOf(level)+1,child=makeLevel(view,index,anchor);child.instance_id=level.instance_id;child.loading=false;
    for(const root of level.listing.roots)folderRow(view,child,root);
    positionSubmenus();if(keyboard)focusLevel(child);
  }
  function renderFolderLevel(view,level){
    level.menu.replaceChildren(level.back);level.menu.setAttribute('aria-label',level.listing.current.name);
    const current=levelButton(level,level.root?'目前工作區':`儲存於「${level.listing.current.name}」`,()=>saveToDestination(view,level.instance_id,level.listing.current.id));current.title=level.listing.current.path;
    if(level.listing.folders.length){levelRule(level);for(const folder of level.listing.folders)folderRow(view,level,folder);}
    if(level.root&&level.listing.roots.length>1){levelRule(level);const other=levelButton(level,'其他位置',()=>showRoots(view,level,other,true),'submenu-trigger');other.setAttribute('aria-haspopup','menu');other.setAttribute('aria-expanded','false');hoverOpen(other,()=>showRoots(view,level,other,false));submenuOpeners.set(other,()=>showRoots(view,level,other,true));}
    positionSubmenus();
  }
  async function browseFolder(view,index,anchor,instanceId,folderId,keyboard=false,root=false){
    if(!destinationValid(view))return;
    const existing=view.levels[index];if(existing?.instance_id===instanceId&&existing.folder_id===folderId&&existing.anchor===anchor){if(keyboard)focusLevel(existing);return;}
    const level=makeLevel(view,index,anchor);level.instance_id=instanceId;level.folder_id=folderId;level.root=root;levelText(level,'讀取位置…');positionSubmenus();if(keyboard){level.menu.tabIndex=-1;level.menu.focus({preventScroll:true});}
    try{
      if(!instances.find(item=>item.instance_id===instanceId)?.capabilities?.includes('save_destination_v1'))throw Object.assign(new Error('請更新 PCS 後再儲存。'),{code:'unsupported'});
      const listing=await request('destinations',{instance_id:instanceId,...(folderId?{folder_id:folderId}:{})});
      if(!levelValid(view,level))return;level.listing=listing;level.loading=false;renderFolderLevel(view,level);
      if(root&&index===0&&view.directClick&&!listing.folders.length){view.directClick=false;await saveToDestination(view,instanceId,listing.current.id);return;}
      view.directClick=false;if(keyboard||view.focusOnReady)focusLevel(level);view.focusOnReady=false;
    }catch(error){if(levelValid(view,level)){view.directClick=false;level.loading=false;level.menu.replaceChildren(level.back);levelText(level,error.message);if(error.code!=='unsupported')levelButton(level,'重新讀取',()=>{trimLevels(view,index);browseFolder(view,index,anchor,instanceId,folderId,true,root);});positionSubmenus();}}
  }
  async function chooseDestination(clicked=false,keyboard=false){
    clearTimeout(hoverTimer);clearTimeout(closeTimer);
    if(operationLocked()||busy||!snapshot||!selected.size)return;
    if(snapshot.context.url!==location.href){closeDestination();notice='對話已切換，請重新選取。';render();return;}
    if(destinationValid(destination||{})){
      const view=destination,first=view.levels[0];
      if(clicked&&instances.length===1&&first?.listing&&!first.listing.folders.length)return saveToDestination(view,first.instance_id,first.listing.current.id);
      if(clicked&&first?.loading)view.directClick=true;
      if(keyboard||clicked){view.focusOnReady=true;if(first&&!first.loading)focusLevel(first);}return;
    }
    closeDestination();
    const view={intent:{generation,url:location.href,context:snapshot.context,capture:selectCapture(snapshot.capture,selected)},levels:[],anchor:null,directClick:clicked,focusOnReady:keyboard||clicked};
    destination=view;notice='';render();
    const loading=makeLevel(view,0,view.anchor);levelText(loading,'讀取位置…');positionSubmenus();
    try{
      const hello=await request('hello');if(!destinationValid(view))return;instances=hello.instances||[];
      if(!instances.length)throw new Error('請開啟 PCS。');
      if(instances.length===1){await browseFolder(view,0,view.anchor,instances[0].instance_id,undefined,keyboard,true);return;}
      view.directClick=false;loading.loading=false;loading.menu.replaceChildren(loading.back);
      for(const instance of instances){const item=levelButton(loading,instance.display_name,()=>browseFolder(view,1,item,instance.instance_id,undefined,true,true),'submenu-trigger');item.setAttribute('aria-haspopup','menu');item.setAttribute('aria-expanded','false');hoverOpen(item,()=>browseFolder(view,1,item,instance.instance_id,undefined,false,true));submenuOpeners.set(item,()=>browseFolder(view,1,item,instance.instance_id,undefined,true,true));}
      positionSubmenus();if(view.focusOnReady)focusLevel(loading);
    }catch(error){if(destinationValid(view)){view.directClick=false;loading.loading=false;loading.menu.replaceChildren(loading.back);levelText(loading,error.code==='host_unavailable'?'請在 PCS 的「加入 Chrome」完成連接。':error.message);levelButton(loading,'重新連接',()=>{closeDestination();chooseDestination(false,true);});positionSubmenus();}}
  }
  async function saveToDestination(view,instanceId,folderId){
    if(operationLocked()||busy||!destinationValid(view))return;
    const identity={operation_id:crypto.randomUUID(),instance_id:instanceId},intended=view.intent;
    closeDestination();clearTimeout(poll);clearTimeout(saveTimer);notice='';pending={...identity,state:'receiving',source_title:intended.capture.title};saveDeadline=Date.now()+30000;saveTimer=setTimeout(()=>unconfirmed(identity),30000);render();
    // PCS canonicalizes UUIDs to 32 characters. Keep the UI request identity:
    // allowing the reply to replace it prevented finally/poll from ever running.
    try{const result=await request('transfer',{...identity,destination_id:folderId,context:intended.context,capture:intended.capture});if(accepts(identity)){pending={...pending,...result,...identity};notice=result.state==='failed'?result.message||'保存失敗。':'';}}
    catch(error){if(accepts(identity)){
      if(error.code==='page_changed'){pending=null;selected.clear();snapshot=null;removeChecks();notice='對話已更新，請重新選取。';}
      else if(['destination_changed','destination_limit','invalid_request'].includes(error.code)){pending={...pending,state:'failed',message:error.message};notice=error.message;}
      else{pending={...pending,...identity,state:'unknown'};notice=error.message;}
    }}finally{if(pending?.operation_id===identity.operation_id){if(!FINAL_STATES.has(pending.state)&&pending.state!=='unknown')schedulePoll();else clearTimeout(saveTimer);}render();}
  }
  function accepts(identity){return pending?.operation_id===identity.operation_id&&pending?.instance_id===identity.instance_id&&!FINAL_STATES.has(pending.state);}
  function unconfirmed(identity){if(!accepts(identity))return;clearTimeout(poll);clearTimeout(saveTimer);pending={...pending,state:'unknown'};notice='';render();}
  function schedulePoll(){clearTimeout(poll);if(Date.now()>=saveDeadline){unconfirmed(pending);return;}poll=setTimeout(checkStatus,2000);}
  async function checkStatus(explicit=false){if(!pending)return;const identity={operation_id:pending.operation_id,instance_id:pending.instance_id},key=identity.instance_id+':'+identity.operation_id;if(statusRequests.has(key))return;statusRequests.add(key);try{const result=await request('status',identity);if(!accepts(identity))return;pending={...pending,...result,...identity};notice=result.state==='failed'?result.message||'保存失敗。':'';if(!FINAL_STATES.has(pending.state)&&pending.state!=='unknown'){if(explicit&&Date.now()>=saveDeadline)unconfirmed(identity);else schedulePoll();}else{clearTimeout(saveTimer);loadRecents();}}catch(error){if(accepts(identity)){clearTimeout(saveTimer);pending={...pending,...identity,state:'unknown'};notice=error.message;}}finally{statusRequests.delete(key);}render();}
  function newSelection(){cancelSelection();}
  async function receiptAction(action,receipt){notice='';try{await request(receipt.kind==='download'?`download_${action}`:action,receipt.kind==='download'?{download_id:receipt.download_id}:{operation_id:receipt.operation_id,instance_id:receipt.instance_id});if(action==='status'){await loadRecents();render();}}catch(error){notice=error.message;render();}}
  async function showRecents(){closeDestination();notice='';await loadRecents();menuMode='recent';render();pollHistory();}
  function pollHistory(){clearTimeout(historyTimer);if(menuOpen&&menuMode==='recent'&&recents.some(r=>r.kind==='download'&&r.state==='in_progress'))historyTimer=setTimeout(async()=>{await loadRecents();if(menuOpen&&menuMode==='recent'){render();pollHistory();}},1500);}
  async function loadRecents(){try{recents=await request('recents');}catch{recents=[];}}
  function scheduleRefresh(){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh(),500);}
  let observedConversationRoot=locateConversation().main;
  function sourcePopovers(root){
    const nodes=new Set();if(!root)return nodes;
    // Match capture.mjs's source triggers and their explicit ARIA ownership.
    for(const trigger of root.querySelectorAll('[data-d-component="popover-trigger"][data-d-inline],[data-testid*="citation"],[data-citation],.citation')){
      if(!trigger.matches('[data-testid*="citation"],[data-citation],.citation')&&!trigger.querySelector('[data-d-component="badge"]'))continue;
      for(const id of `${trigger.getAttribute('aria-controls')||''} ${trigger.getAttribute('aria-describedby')||''}`.trim().split(/\s+/)){
        const node=document.getElementById(id);if(node&&node!==trigger&&node.matches('[role="dialog"],[role="tooltip"],[data-radix-popper-content-wrapper]'))nodes.add(node);
      }
    }return nodes;
  }
  let observedSourcePopovers=sourcePopovers(observedConversationRoot);
  const observer=new MutationObserver(records=>{
    let currentRoot;try{currentRoot=locateConversation().main;}catch{currentRoot=observedConversationRoot;}
    const roots=[currentRoot,observedConversationRoot].filter(Boolean);
    const currentPopovers=sourcePopovers(currentRoot),popovers=new Set(currentPopovers);
    if(currentRoot===observedConversationRoot)for(const node of observedSourcePopovers)popovers.add(node);
    const inPopover=node=>[...popovers].some(popup=>popup===node||popup.contains(node));
    const onlyPopover=(node,depth=0)=>inPopover(node)||depth>0&&node.nodeType===3&&!node.textContent.trim()||
      node.nodeType===1&&depth<8&&node.childNodes.length>0&&node.childNodes.length<=32&&[...popovers].some(popup=>node.contains(popup))&&[...node.childNodes].every(child=>onlyPopover(child,depth+1));
    const relevant=records.some(r=>{
      if(r.type==='attributes')return false;
      const target=r.target.nodeType===1?r.target:r.target.parentElement;
      if(target?.closest('[data-pcs-ui]'))return false;
      const changed=[...r.addedNodes,...r.removedNodes].filter(n=>!(n.nodeType===1&&n.matches('[data-pcs-ui]')));
      // Floating source cards and unrelated app chrome do not change message
      // content. In particular, inspecting a source card must not trigger an
      // endless capture -> popup -> capture loop. Root replacements still count.
      if(changed.some(n=>roots.some(root=>n===root||n.nodeType===1&&n.contains(root))))return true;
      if(!roots.some(root=>root.contains(target))||inPopover(target))return false;
      return r.type==='characterData'||changed.some(node=>!onlyPopover(node));
    });
    observedConversationRoot=currentRoot;observedSourcePopovers=currentPopovers;mount();positionChecks();if(active&&relevant)scheduleRefresh();
  });observer.observe(document.body,{childList:true,subtree:true,characterData:true,attributes:true,attributeFilter:['aria-controls','aria-describedby']});
  let frame=0;const reposition=()=>{if(!frame)frame=requestAnimationFrame(()=>{frame=0;positionChecks();positionMenu();positionSubmenus();});};addEventListener('scroll',reposition,true);addEventListener('resize',reposition);
  document.addEventListener('pointerdown',e=>{if(!e.isTrusted||e.composedPath().some(n=>n instanceof Element&&n.hasAttribute('data-pcs-ui')))return;closeDestination();menuOpen=false;render();},true);
  menu.addEventListener('pointerover',event=>{if(!event.isTrusted||!destination)return;const target=event.target.closest('button');if(target&&target!==destination.anchor){clearTimeout(hoverTimer);hoverTimer=setTimeout(()=>{closeDestination();render();},180);}});
  document.addEventListener('pointermove',event=>{if(!event.isTrusted||!destination)return;const path=event.composedPath(),inside=path.includes(popup.element)||destination.levels.some(level=>path.includes(level.host.element));if(inside){clearTimeout(closeTimer);closeTimer=null;}else if(!closeTimer)closeTimer=setTimeout(()=>{closeDestination();render();},350);},true);
  document.addEventListener('keydown',event=>{
    if(!event.isTrusted)return;
    if(!menuOpen){if(event.key==='Escape'&&active&&!busy&&!operationLocked()){event.preventDefault();cancelSelection();}return;}
    const target=event.composedPath().find(node=>node instanceof HTMLButtonElement),level=destination?.levels.find(item=>event.composedPath().includes(item.host.element));
    const inside=level||event.composedPath().includes(popup.element);
    if(inside&&event.key==='ArrowRight'&&submenuOpeners.has(target)){event.preventDefault();submenuOpeners.get(target)();return;}
    // Rendering replaces focused buttons before the next animation frame.
    // Escape still belongs to the open submenu while focus briefly rests on body.
    if(destination&&(event.key==='Escape'||inside&&event.key==='ArrowLeft')){event.preventDefault();const current=level||(event.key==='Escape'?destination.levels.at(-1):null),anchor=current?.anchor||destination.anchor;if(current)trimLevels(destination,destination.levels.indexOf(current));else closeDestination();if(destination&&!destination.levels.length)closeDestination();anchor?.focus({preventScroll:true});return;}
    if(inside&&['ArrowDown','ArrowUp','Home','End'].includes(event.key)){event.preventDefault();const items=[...(level?.menu||menu).querySelectorAll('button:not(:disabled):not([hidden])')],index=items.indexOf(target);const next=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next]?.focus({preventScroll:true});return;}
    if(event.key==='Escape'){closeDestination();if(active&&!busy&&!operationLocked())cancelSelection();menuOpen=false;render();menuAnchor?.shadowRoot?.querySelector('button')?.focus({preventScroll:true});}
  },true);
  setInterval(()=>{if(location.href!==lastUrl){generation++;lastUrl=location.href;closeDestination();clearOutcome();menuOpen=false;selected.clear();snapshot=null;removeChecks();if(operationLocked()){active=false;menuAnchor=entry.element;menuMode='selection';}else if(active){notice='對話已切換，請重新選取。';refresh();}else{menuMode='choices';notice='';}render();}mount();},750);
  chrome.runtime.onMessage.addListener(message=>{if(message.action==='pcs-toggle-selection')openHeader();});mount();render();controller={toggle:openHeader};return controller;
}
