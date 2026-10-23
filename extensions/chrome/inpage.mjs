import {selectCapture,markdownDocument,downloadName,statusLabel,FINAL_STATES} from './core.mjs';
import {locateConversation,messageCopyControl,conversationHeaderSlot} from './dom.mjs';

let controller;
const CSS=`:host{all:initial;font:14px/1.45 system-ui,'Segoe UI','Microsoft JhengHei',sans-serif;--bg:#2f2f2f;--text:#ececec;--muted:#aaa;--line:#ffffff24;--hover:#ffffff12;color:var(--text);color-scheme:dark}:host([data-theme=light]){--bg:#fff;--text:#292929;--muted:#666;--line:#0002;--hover:#0000000a;color-scheme:light}*{box-sizing:border-box}[hidden]{display:none!important}button,select{font:inherit;color:inherit;cursor:pointer}button{background:transparent;border:0;border-radius:6px;min-height:32px;padding:5px 8px;text-align:left}button:hover{background:var(--hover)}button:disabled{opacity:.4;cursor:default}button:focus-visible,select:focus-visible{outline:2px solid var(--muted);outline-offset:2px}.entry{color:var(--muted);font-size:13px;padding:4px 7px}.entry[aria-expanded=true]{background:var(--hover);color:var(--text)}.menu{width:248px;max-width:calc(100vw - 20px);padding:6px;background:var(--bg);border:1px solid var(--line);border-radius:12px;box-shadow:0 4px 16px #0003;max-height:calc(100vh - 24px);overflow:auto}.menu>button{display:block;width:100%;padding:8px 10px}.title{font-size:12px;color:var(--muted);padding:5px 10px}.section{height:1px;background:var(--line);margin:5px 4px}.tools{display:flex;justify-content:space-between;padding:0 3px}.tools button{font-size:12px;color:var(--muted)}.status{padding:8px 10px;font-size:13px}.receipt{padding:5px 7px}.receipt span{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.receipt button{font-size:12px;color:var(--muted)}select{width:calc(100% - 20px);margin:4px 10px 8px;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg)}.destination-current{padding:8px 10px 4px;font-weight:600;overflow-wrap:anywhere}.folders{max-height:min(240px,38vh);overflow-y:auto;overscroll-behavior:contain}.folders button{display:flex;width:100%;gap:8px;align-items:center;padding:8px 10px;overflow-wrap:anywhere}.folder:before{content:"";display:block;flex:0 0 16px;width:16px;height:16px;background:currentColor;opacity:.65;mask:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24'%3E%3Cpath d='M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z' fill='none' stroke='black' stroke-width='1.7' stroke-linejoin='round'/%3E%3C/svg%3E") center/contain no-repeat}.menu>button.save{font-weight:600;background:var(--hover)}.check{display:flex;align-items:center;justify-content:center;width:32px;height:32px;cursor:pointer}.check input{appearance:none;width:20px;height:20px;margin:0;border:1px solid var(--muted);border-radius:4px;background:transparent;cursor:pointer;position:relative}.check input:checked{background:var(--text);border-color:var(--text)}.check input:checked:after{content:'';position:absolute;left:6px;top:2px;width:5px;height:10px;border:solid var(--bg);border-width:0 2px 2px 0;transform:rotate(45deg)}.check input:focus-visible{outline:2px solid var(--text);outline-offset:3px}.check input:disabled{opacity:.45;cursor:default}`;

function host(kind){const element=document.createElement('span');element.dataset.pcsUi=kind;const root=element.attachShadow({mode:'open'});const style=document.createElement('style');style.textContent=CSS+':host([hidden]){display:none!important}';root.append(style);return {element,root};}
function button(text,action,className=''){const result=document.createElement('button');result.type='button';result.textContent=text;result.className=className;result.addEventListener('click',e=>{if(e.isTrusted)action();});return result;}
function visible(element){for(let node=element;node;node=node.parentElement){const s=getComputedStyle(node);if(node.hidden||node.getAttribute('aria-hidden')==='true'||s.display==='none'||s.visibility==='hidden')return false;}return true;}
function messageElements(){try{return locateConversation().messages.map(item=>item.element);}catch{return [];}}

export function installInPage(){
  if(controller)return controller;
  if(!chrome?.runtime?.id)throw new Error('PCS extension context unavailable');
  let active=false,snapshot=null,selected=new Set(),checks=[],pending=null,busy=false,notice='',poll=null;
  let instances=[],recents=[],lastUrl=location.href,refreshTimer=null,reading=false,refreshAgain=false,generation=0;
  let destination=null,destinationRequest=0;
  let menuOpen=false,menuMode='choices',menuAnchor=null,sourceAnchor=null;
  let selectionHandlers=null,selectionStyle=null,press=null,clickTimer=null,lastClick=null;
  const selectionToken=chrome.runtime.id;
  const messageButtons=new Map(),entry=host('entry'),popup=host('menu');
  entry.element.style.cssText='display:inline-flex;vertical-align:middle;align-self:center;flex-shrink:0;margin-inline:2px;pointer-events:auto;';
  popup.element.style.cssText='position:fixed;z-index:2147483000;';
  const headerButton=button('PCS',openHeader,'entry');headerButton.setAttribute('aria-haspopup','dialog');entry.root.append(headerButton);
  const menu=document.createElement('div');menu.className='menu';menu.setAttribute('role','dialog');menu.setAttribute('aria-label','PCS');popup.root.append(menu);

  async function request(action,fields={}){let response;try{response=await chrome.runtime.sendMessage({action,...fields});}catch(error){throw Object.assign(new Error('擴充已更新，請重新整理頁面。'),{code:'extension_unavailable',cause:error});}if(!response?.ok)throw Object.assign(new Error(response?.error?.message||'操作失敗，請再試一次。'),{code:response?.error?.code||'failed'});return response.result;}
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
    const value=theme();for(const item of [entry.element,popup.element,...checks.map(c=>c.host),...[...messageButtons.values()].map(c=>c.element)])item.dataset.theme=value;
    positionMenu();
  }
  function positionMenu(){if(!menuOpen||!menuAnchor?.isConnected)return;popup.element.hidden=menuAnchor===entry.element&&entry.element.hidden;if(popup.element.hidden)return;const rect=menuAnchor.getBoundingClientRect(),height=menu.getBoundingClientRect().height,width=menu.getBoundingClientRect().width;popup.element.style.left=`${Math.max(10,Math.min(innerWidth-width-10,rect.right-width))}px`;popup.element.style.top=`${Math.max(10,rect.bottom+6+height>innerHeight?rect.top-height-6:rect.bottom+6)}px`;}
  function separator(){const line=document.createElement('div');line.className='section';menu.append(line);}
  function caption(text){const node=document.createElement('div');node.className='title';node.textContent=text;menu.append(node);}
  function render(){
    const focused=popup.root.activeElement,focusLabel=focused?.getAttribute('aria-label')||focused?.textContent;
    headerButton.textContent=active&&selected.size?`PCS ${selected.size}`:'PCS';headerButton.setAttribute('aria-expanded',String(menuOpen&&menuAnchor===entry.element));
    popup.element.hidden=!menuOpen||(menuAnchor===entry.element&&entry.element.hidden);menu.replaceChildren();syncChecks();for(const item of checks)item.input.disabled=busy||!!pending||!!destination;for(const item of messageButtons.values())item.root.querySelector('button').disabled=busy||!!destination||!!(pending&&!FINAL_STATES.has(pending.state));
    if(menuMode==='destination'&&destination){
      renderDestination();
    }else if(menuMode==='recent'){
      menu.append(button('‹ 返回',()=>{menuMode=active||snapshot?'selection':'choices';render();}));caption('最近保存');const saved=recents.filter(r=>r.state==='committed').slice(0,3);if(!saved.length)caption('尚無紀錄');
      for(const receipt of saved){const row=document.createElement('div');row.className='receipt';const name=document.createElement('span');name.textContent=receipt.title;row.append(name,button('開啟文件',()=>receiptAction('open',receipt)),button('顯示資料夾',()=>receiptAction('reveal',receipt)));menu.append(row);}
    }else if(menuMode==='choices'&&!pending){
      menu.append(button('選取全部',()=>startSelection(true)),button('選取訊息',()=>startSelection(false)));
      if(notice){const text=document.createElement('div');text.className='status';text.textContent=notice;menu.append(text);}separator();menu.append(button('最近保存',showRecents));
    }else{
      caption(pending?(pending.source_title||pending.title||'傳送結果'):menuMode==='message'?'此則訊息':`已選 ${selected.size} 則`);
      if(active&&!pending){const tools=document.createElement('div');tools.className='tools';const all=button('全選',()=>{selected=new Set(snapshot?.capture.messages.map(m=>m.id)||[]);syncChecks();render();});all.disabled=busy;const done=button('完成選取',finishSelection);done.disabled=busy;tools.append(all,done);menu.append(tools);}
      if(!pending){const send=button('儲存到 PCS',chooseDestination);send.disabled=!selected.size||busy;menu.append(send);
      const json=button('下載 JSON',()=>downloadSelected('json')),md=button('下載 Markdown',()=>downloadSelected('md'));json.disabled=md.disabled=!selected.size||busy;menu.append(json,md);}
      if(notice||pending){separator();const text=document.createElement('div');text.className='status';text.setAttribute('aria-live','polite');text.textContent=notice||(pending?(pending.state==='failed'&&pending.message||statusLabel(pending.state)):'');menu.append(text);}
      if(pending){if(pending.state==='committed')menu.append(button('開啟文件',()=>receiptAction('open',pending)),button('顯示資料夾',()=>receiptAction('reveal',pending)));else if(!FINAL_STATES.has(pending.state)){if(pending.state==='unknown')menu.append(button('查詢結果',checkStatus));menu.append(button('取消',cancel));}if(FINAL_STATES.has(pending.state)||pending.state==='unknown')menu.append(button('重新選取',newSelection));}
      else if(notice&&!busy)menu.append(button('重新讀取',retrySelection));separator();menu.append(button('最近保存',showRecents));
    }requestAnimationFrame(()=>{positionMenu();if(menuOpen&&focusLabel){const replacement=[...menu.querySelectorAll('button,select')].find(item=>(item.getAttribute('aria-label')||item.textContent)===focusLabel&&!item.disabled);(replacement||menu.querySelector('button:not(:disabled),select'))?.focus({preventScroll:true});}});
  }
  function focusMenu(){requestAnimationFrame(()=>{if(menuOpen)menu.querySelector('button:not(:disabled),select')?.focus({preventScroll:true});});}
  async function openHeader(){if(menuOpen&&menuAnchor===entry.element){menuOpen=false;render();return;}menuAnchor=entry.element;menuMode=destination?'destination':pending||active||(snapshot&&!sourceAnchor)?'selection':'choices';menuOpen=true;notice='';await loadRecents();render();focusMenu();}
  async function openMessage(anchor,buttonHost){if(busy||destination||(pending&&!FINAL_STATES.has(pending.state)))return;pending=null;clearTimeout(poll);generation++;const expected=generation,url=location.href;active=false;selected.clear();snapshot=null;removeChecks();sourceAnchor=anchor;menuAnchor=buttonHost;menuMode='message';menuOpen=true;busy=true;notice='';render();try{const fresh=await request('capture');if(generation!==expected||location.href!==url||fresh.context.url!==url)return;const index=messageElements().indexOf(anchor),id=fresh.anchors.find(a=>a.index===index)?.id;if(!id)throw new Error('訊息已更新，請再選一次。');snapshot=fresh;selected=new Set([id]);}catch(error){if(generation===expected)notice=error.message;}finally{busy=false;render();focusMenu();}}
  async function startSelection(all){generation++;active=true;sourceAnchor=null;menuAnchor=entry.element;selected.clear();snapshot=null;menuMode='selection';notice='';await refresh(true);if(all&&snapshot)selected=new Set(snapshot.capture.messages.map(m=>m.id));syncChecks();render();if(!all&&snapshot){menuOpen=false;render();}}
  function finishSelection(){generation++;active=false;removeChecks();menuMode='selection';notice='';render();focusMenu();}
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
  function toggleItem(item){if(!active||busy||pending||destination||!checks.includes(item))return;if(selected.has(item.id))selected.delete(item.id);else selected.add(item.id);notice='';syncChecks();render();}
  function selectionItem(event){
    if(!active||busy||pending||destination||!event.isTrusted||event.defaultPrevented||event.ctrlKey||event.metaKey||event.altKey||event.shiftKey)return null;
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
      const expected=generation;clickTimer=setTimeout(()=>{clickTimer=null;if(generation!==expected||!getSelection()?.isCollapsed||!active||busy||pending||destination)return;lastClick={item,generation,wasSelected:selected.has(item.id)};toggleItem(item);},250);
    },options);
  }
  function buildChecks(){
    removeChecks();if(!active||!snapshot)return;const elements=messageElements();
    for(const item of snapshot.anchors||[]){
      const anchor=elements[item.index];if(!anchor)continue;const marker=host('selection');marker.element.style.cssText='position:fixed;z-index:2147482999;';
      const label=document.createElement('label');label.className='check';const input=document.createElement('input');input.type='checkbox';input.setAttribute('aria-label',`選取第 ${item.index+1} 則訊息`);input.checked=selected.has(item.id);
      input.addEventListener('change',e=>{if(!e.isTrusted)return;cancelClick();lastClick=null;if(pending||busy||destination){input.checked=selected.has(item.id);return;}toggleItem(checks.find(check=>check.input===input));});
      label.append(input);marker.root.append(label);document.body.append(marker.element);
      const originalSelectable=anchor.getAttribute('data-pcs-selectable'),originalSelected=anchor.getAttribute('data-pcs-selected');anchor.setAttribute('data-pcs-selectable',selectionToken);
      checks.push({id:item.id,anchor,input,host:marker.element,originalSelectable,originalSelected});
    }
    bindSelection();syncChecks();mount();positionChecks();
  }
  async function refresh(explicit=false){if(!active||pending||destination)return;if(reading){refreshAgain=true;return;}reading=true;const expected=generation,url=location.href;try{const fresh=await request('capture');if(!active||destination||generation!==expected||location.href!==url||fresh.context.url!==url)return;const changed=snapshot&&(fresh.fingerprint!==snapshot.fingerprint||fresh.context.url!==snapshot.context.url||fresh.context.tabId!==snapshot.context.tabId);if(changed){selected.clear();notice='對話已更新，請重新選取。';}else if(explicit)notice='';snapshot=fresh;buildChecks();}catch(error){if(generation===expected&&active){snapshot=null;selected.clear();removeChecks();notice=error.message;menuOpen=true;}}finally{reading=false;render();if(refreshAgain){refreshAgain=false;scheduleRefresh();}}}
  async function downloadSelected(ext){if(busy||!snapshot)return;busy=true;render();try{const fresh=await request('capture');if(!snapshot||fresh.fingerprint!==snapshot.fingerprint||fresh.context.url!==snapshot.context.url){selected.clear();notice='對話已更新，請重新選取。';await refresh();return;}const capture=selectCapture(snapshot.capture,selected);const blob=new Blob([ext==='json'?JSON.stringify(capture,null,2):markdownDocument(capture)],{type:ext==='json'?'application/json;charset=utf-8':'text/markdown;charset=utf-8'});const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=downloadName(capture.title,ext);link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(error){notice=error.message;}finally{busy=false;render();}}
  function statusText(text){const node=document.createElement('div');node.className='status';node.setAttribute('aria-live','polite');node.textContent=text;menu.append(node);}
  function selectionValid(intent){return generation===intent.generation&&location.href===intent.url&&snapshot?.context.fingerprint===intent.context.fingerprint;}
  function destinationValid(view){return destination===view&&selectionValid(view.intent);}
  function destinationSelect(label,items,value,change){
    const choice=document.createElement('select');choice.setAttribute('aria-label',label);choice.disabled=!destination.ready;
    for(const item of items){const option=document.createElement('option');option.value=item.id;option.textContent=item.name;choice.append(option);}
    choice.value=value;choice.addEventListener('change',event=>{if(event.isTrusted)change(choice.value);});menu.append(choice);
  }
  function renderDestination(){
    const view=destination;menu.append(button('‹ 返回',leaveDestination));
    if(instances.length>1)destinationSelect('PCS 工作區',[{id:'',name:'選擇 PCS 工作區'},...instances.map(item=>({id:item.instance_id,name:item.display_name}))],view.instance_id,id=>{view.instance_id=id;view.listing=null;view.root_id=null;view.error='';destinationRequest++;if(id)loadDestination(view);else{view.loading=false;render();}});
    const listing=view.listing;
    if(listing){
      if(listing.roots.length>1){
        const path=listing.current.path.replaceAll('\\','/').toLowerCase();
        const root=[...listing.roots].sort((a,b)=>b.path.length-a.path.length).find(item=>{const prefix=item.path.replaceAll('\\','/').toLowerCase().replace(/\/$/,'');return path===prefix||path.startsWith(prefix+'/');});
        destinationSelect('位置',listing.roots,view.loading&&view.root_id||root?.id||'',id=>{view.root_id=id;loadDestination(view,id);});
      }
      const current=document.createElement('div');current.className='destination-current';current.textContent=listing.current.name;current.title=listing.current.path;menu.append(current);
      const folders=document.createElement('div');folders.className='folders';folders.setAttribute('aria-label','資料夾');
      if(listing.current.parent_id){const up=button('上一層',()=>loadDestination(view,listing.current.parent_id));up.disabled=view.loading;folders.append(up);}
      for(const folder of listing.folders){const row=button(folder.name,()=>loadDestination(view,folder.id),'folder');row.title=folder.path;row.disabled=view.loading;folders.append(row);}
      if(!listing.folders.length&&!view.loading){const empty=document.createElement('div');empty.className='title';empty.textContent='沒有子資料夾';folders.append(empty);}
      menu.append(folders);
    }
    if(view.loading)statusText('讀取位置…');
    if(view.error){statusText(view.error);if(view.instance_id&&!view.unsupported)menu.append(button('重新讀取',()=>loadDestination(view)));else if(!view.instance_id&&!instances.length)menu.append(button('重新連接',()=>{leaveDestination();chooseDestination();}));}
    separator();const save=button('儲存',saveToDestination,'save');save.disabled=view.loading||!listing||!!view.error;menu.append(save);
  }
  function leaveDestination(){const mode=destination?.returnMode||'selection';destination=null;destinationRequest++;busy=false;notice='';menuMode=mode;render();focusMenu();}
  async function chooseDestination(){
    if(pending||busy||!snapshot||!selected.size)return;
    const view={intent:{generation,url:location.href,context:snapshot.context,capture:selectCapture(snapshot.capture,selected)},returnMode:menuMode,instance_id:'',listing:null,loading:true,ready:false,error:''};
    destination=view;menuMode='destination';notice='';render();focusMenu();
    try{
      const hello=await request('hello');if(!destinationValid(view))return;instances=hello.instances||[];view.ready=true;
      if(!instances.length){view.error='請開啟 PCS。';view.loading=false;return;}
      const preference=hello.preferred_destination;
      view.instance_id=instances.length===1?instances[0].instance_id:instances.find(item=>item.instance_id===preference?.instance_id)?.instance_id||'';
      if(view.instance_id)await loadDestination(view,preference?.instance_id===view.instance_id?preference.folder_id:undefined,true);
      else view.loading=false;
    }catch(error){if(destinationValid(view)){view.error=error.code==='host_unavailable'?'請在 PCS 的「加入 Chrome」完成連接。':error.message;view.loading=false;}}
    finally{if(destinationValid(view))render();}
  }
  async function loadDestination(view,folderId,allowDefault=false){
    if(!destinationValid(view))return;
    const serial=++destinationRequest;view.loading=true;view.error='';view.unsupported=false;render();
    try{
      if(!instances.find(item=>item.instance_id===view.instance_id)?.capabilities?.includes('save_destination_v1')){view.unsupported=true;throw new Error('請更新 PCS 後再儲存。');}
      let listing;
      try{listing=await request('destinations',{instance_id:view.instance_id,...(folderId?{folder_id:folderId}:{})});}
      catch(error){if(!allowDefault||!folderId||error.code!=='destination_changed'||!destinationValid(view)||serial!==destinationRequest)throw error;listing=await request('destinations',{instance_id:view.instance_id});}
      if(!destinationValid(view)||serial!==destinationRequest)return;
      view.listing=listing;
    }catch(error){if(destinationValid(view)&&serial===destinationRequest){view.listing=null;view.error=error.message;}}
    finally{if(destinationValid(view)&&serial===destinationRequest){view.loading=false;render();}}
  }
  async function saveToDestination(){
    const view=destination;if(!view||view.loading||!view.listing||view.error||pending)return;
    if(!destinationValid(view)){leaveDestination();notice='對話已更新，請重新選取。';render();return;}
    const identity={operation_id:crypto.randomUUID(),instance_id:view.instance_id},intended=view.intent;
    destination=null;destinationRequest++;menuMode=view.returnMode;busy=true;notice='';pending={...identity,state:'receiving',source_title:intended.capture.title};render();
    try{const result=await request('transfer',{...identity,destination_id:view.listing.current.id,context:intended.context,capture:intended.capture});if(accepts(identity)){pending={...pending,...identity,...result};notice=result.state==='failed'?result.message||'保存失敗。':'';}}
    catch(error){if(accepts(identity)){
      if(error.code==='page_changed'){pending=null;selected.clear();snapshot=null;removeChecks();notice='對話已更新，請重新選取。';}
      else if(['destination_changed','destination_limit','invalid_request'].includes(error.code)){if(selectionValid(intended)){pending=null;destination=view;view.listing=null;view.error=error.message;menuMode='destination';}else{pending={...pending,state:'failed',message:error.message};notice=error.message;}}
      else{pending={...pending,...identity,state:'unknown'};notice=error.message;}
    }}finally{if(!pending||pending.operation_id===identity.operation_id){busy=false;if(pending&&!FINAL_STATES.has(pending.state)&&pending.state!=='unknown')schedulePoll();render();}}
  }
  function accepts(identity){return pending?.operation_id===identity.operation_id&&pending?.instance_id===identity.instance_id&&!FINAL_STATES.has(pending.state);}
  function schedulePoll(){clearTimeout(poll);poll=setTimeout(checkStatus,2000);}
  async function checkStatus(){if(!pending)return;const identity={operation_id:pending.operation_id,instance_id:pending.instance_id};try{const result=await request('status',identity);if(!accepts(identity))return;pending={...pending,...identity,...result};notice=result.state==='failed'?result.message||'保存失敗。':'';if(!FINAL_STATES.has(pending.state)&&pending.state!=='unknown')schedulePoll();else loadRecents();}catch(error){if(accepts(identity)){pending={...pending,...identity,state:'unknown'};notice=error.message;}}render();}
  async function cancel(){if(!pending)return;const identity={operation_id:pending.operation_id,instance_id:pending.instance_id};clearTimeout(poll);try{const result=await request('cancel',identity);if(!accepts(identity))return;pending={...pending,...identity,...result};notice='';if(!FINAL_STATES.has(pending.state)&&pending.state!=='unknown')schedulePoll();}catch(error){if(accepts(identity))notice=error.message;}render();}
  async function newSelection(){if(pending)await request('dismiss',pending).catch(()=>{});clearTimeout(poll);pending=null;selected.clear();snapshot=null;notice='';generation++;active=false;removeChecks();menuMode='choices';render();}
  async function receiptAction(action,receipt){try{await request(action,{operation_id:receipt.operation_id,instance_id:receipt.instance_id});}catch(error){notice=error.message;render();}}
  async function showRecents(){await loadRecents();menuMode='recent';render();}
  async function loadRecents(){try{recents=await request('recents');}catch{recents=[];}if(!pending){const unfinished=recents.find(r=>!r.dismissed&&!FINAL_STATES.has(r.state));if(unfinished){pending=unfinished;menuMode='selection';checkStatus();}}}
  function scheduleRefresh(){clearTimeout(refreshTimer);refreshTimer=setTimeout(()=>refresh(),500);}
  const observer=new MutationObserver(records=>{const relevant=records.some(r=>{const target=r.target.nodeType===1?r.target:r.target.parentElement;if(target?.closest('[data-pcs-ui]'))return false;return r.type==='characterData'||[...r.addedNodes,...r.removedNodes].some(n=>!(n.nodeType===1&&n.matches('[data-pcs-ui]')));});mount();positionChecks();if(active&&relevant)scheduleRefresh();});observer.observe(document.body,{childList:true,subtree:true,characterData:true});
  let frame=0;const reposition=()=>{if(!frame)frame=requestAnimationFrame(()=>{frame=0;positionChecks();positionMenu();});};addEventListener('scroll',reposition,true);addEventListener('resize',reposition);
  document.addEventListener('pointerdown',e=>{if(!e.isTrusted||e.composedPath().some(n=>n instanceof Element&&n.hasAttribute('data-pcs-ui')))return;menuOpen=false;render();},true);
  document.addEventListener('keydown',e=>{if(e.isTrusted&&e.key==='Escape'&&menuOpen){menuOpen=false;render();menuAnchor?.shadowRoot?.querySelector('button')?.focus({preventScroll:true});}},true);
  setInterval(()=>{if(location.href!==lastUrl){generation++;lastUrl=location.href;destination=null;destinationRequest++;selected.clear();snapshot=null;removeChecks();if(pending){active=false;menuOpen=false;menuAnchor=entry.element;menuMode='selection';}else if(active){notice='對話已切換，請重新選取。';refresh();}else{menuMode='choices';notice='';}render();}mount();},750);
  chrome.runtime.onMessage.addListener(message=>{if(message.action==='pcs-toggle-selection')openHeader();});mount();render();controller={toggle:openHeader};return controller;
}
