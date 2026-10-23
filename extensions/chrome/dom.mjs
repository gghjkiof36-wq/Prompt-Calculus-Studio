// Shared by in-page controls. capture.mjs carries the same locator inside its
// serialized function, because chrome.scripting does not preserve module scope.
export function locateConversation() {
  const visible = element => {
    for (let node=element;node;node=node.parentElement) {
      const style=getComputedStyle(node);
      if(node.hidden||node.getAttribute('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden')return false;
    }
    return true;
  };
  function messagesIn(root) {
    const modern=[];
    for(const element of root.querySelectorAll('[data-chatgpt-search-unit-key],[data-content-search-unit-key]')) {
      if(!visible(element)||element.closest('[data-pcs-ui]'))continue;
      const key=element.getAttribute('data-chatgpt-search-unit-key')||element.getAttribute('data-content-search-unit-key');
      const role=key?.match(/:(user|assistant)$/)?.[1];
      if(!role||role==='assistant'&&!element.querySelector('h4[data-conversation-role="assistant"]'))continue;
      const duplicate=modern.find(item=>item.key===key);
      if(duplicate) {
        if(duplicate.element.contains(element))continue;
        if(element.contains(duplicate.element)){duplicate.element=element;continue;}
        throw new Error('對話正在更新，請重新讀取。');
      }
      modern.push({element,role,key});
    }
    const legacy=[...root.querySelectorAll('[data-message-author-role]')].filter(element=>visible(element)&&!element.closest('[data-pcs-ui]'))
      .filter(element=>['user','assistant'].includes(element.getAttribute('data-message-author-role')))
      .filter(element=>!modern.some(item=>item.element.contains(element)||element.contains(item.element)))
      .map(element=>({element,role:element.getAttribute('data-message-author-role'),key:null}));
    return [...modern,...legacy].sort((a,b)=>a.element.compareDocumentPosition(b.element)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1);
  }
  const regions=[...document.querySelectorAll('[role="region"][aria-label="對話"],[role="region"][aria-label="Conversation"]')].filter(visible);
  const mains=[...document.querySelectorAll('main,[role="main"]')].filter(visible);
  for(const roots of [regions,mains]) {
    const candidates=roots.map(main=>({main,messages:messagesIn(main)})).filter(item=>item.messages.length);
    candidates.sort((a,b)=>b.messages.length-a.messages.length);
    if(candidates.length)return candidates[0];
  }
  return {main:mains[0]||regions[0]||null,messages:[]};
}

export function messageCopyControl(element,messages) {
  const selector='button[data-testid="copy-turn-action-button"],button[aria-label="Copy"],button[aria-label="Copy response"],button[aria-label="Copy message"],button[aria-label="複製"],button[aria-label="複製回覆"],button[aria-label="複製訊息"],button[title="Copy"],button[title="複製"]';
  const key=element.getAttribute('data-chatgpt-search-unit-key')||element.getAttribute('data-content-search-unit-key');
  const eligible=button=>{
    if(button.closest('pre,code,[data-code-block],[data-testid*="code-block"]'))return false;
    for(let node=button;node;node=node.parentElement){const style=getComputedStyle(node);if(node.hidden||node.getAttribute('aria-hidden')==='true'||style.display==='none'||style.visibility==='hidden')return false;}
    return true;
  };
  const find=scope=>[...scope.querySelectorAll(selector)].filter(eligible).filter(button=>!key||button.closest('.turn-action-controls'));
  for(let scope=element;scope&&!scope.matches('main,[role="main"],[role="region"]');scope=scope.parentElement) {
    if(messages.some(other=>other!==element&&scope.contains(other)))break;
    const candidates=find(scope);
    if(candidates.length===1)return candidates[0];
    if(candidates.length>1)return null;
    if(scope.matches('[data-content-search-turn-key],article,[data-testid^="conversation-turn"]'))break;
  }
  // New ChatGPT renders the reply controls as a sibling of the entire
  // user/thought/assistant group. Only a unique, unowned row in this same turn
  // can belong to the assistant; never fall through to a neighbouring turn.
  if(!key?.endsWith(':assistant'))return null;
  const turn=element.closest('[data-content-search-turn-key]');
  const group=element.closest('[data-talvt-turn-state]')||turn;
  if(!turn||!group||!turn.contains(group))return null;
  const assistants=messages.filter(message=>group.contains(message)&&
    (message.getAttribute('data-chatgpt-search-unit-key')||message.getAttribute('data-content-search-unit-key'))?.endsWith(':assistant'));
  if(assistants.length!==1||assistants[0]!==element)return null;
  const candidates=[];
  for(const row of group.querySelectorAll('.turn-action-controls')){
    if(row.closest('[data-content-search-turn-key]')!==turn)continue;
    let ownedByOther=false;
    for(let scope=row;scope&&scope!==group;scope=scope.parentElement){
      if(messages.some(other=>other!==element&&scope.contains(other))||messages.some(other=>other!==element&&other.contains(scope))){ownedByOther=true;break;}
    }
    if(ownedByOther)continue;
    candidates.push(...find(row).filter(button=>!['複製訊息','Copy message'].includes(button.getAttribute('aria-label'))));
  }
  if(candidates.length===1)return candidates[0];
  return null;
}
