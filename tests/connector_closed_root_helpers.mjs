// Debugger-only access for Playwright locators. The shipped extension is used
// byte-for-byte. The website's MAIN world is never patched and cannot use this
// accessor; security assertions must use page.evaluate (MAIN world).
export async function closedRootAutomation(context,page){
  const cdp=await context.newCDPSession(page);let world=null,closing=false,pending=null;
  cdp.on('Runtime.executionContextCreated',({context})=>{
    if(context.name.includes('playwright_utility_world'))world=context.id;
  });
  cdp.on('Runtime.executionContextDestroyed',({executionContextId})=>{if(world===executionContextId)world=null;});
  cdp.on('Runtime.executionContextsCleared',()=>{world=null;});
  await cdp.send('Runtime.enable');
  async function synchronize(){
    if(!world||closing)return;
    const {root}=await cdp.send('DOM.getDocument',{depth:-1,pierce:true});
    const pairs=[];
    function visit(node){
      for(const shadow of node.shadowRoots||[]){
        if(shadow.shadowRootType==='closed'&&(node.attributes||[]).includes('data-pcs-ui'))pairs.push([node,shadow]);
        visit(shadow);
      }
      for(const child of node.children||[])visit(child);
      if(node.contentDocument)visit(node.contentDocument);
    }
    visit(root);if(!pairs.length)return;
    await cdp.send('Runtime.evaluate',{contextId:world,expression:`(()=>{
      if(globalThis.__pcsAutomationRoots)return;
      globalThis.__pcsAutomationRoots=new WeakMap();
      const original=Object.getOwnPropertyDescriptor(Element.prototype,'shadowRoot').get;
      Object.defineProperty(Element.prototype,'shadowRoot',{configurable:true,get(){return globalThis.__pcsAutomationRoots.get(this)||original.call(this);}});
    })()`});
    for(const [host,shadow] of pairs){
      const h=await cdp.send('DOM.resolveNode',{backendNodeId:host.backendNodeId,executionContextId:world});
      const s=await cdp.send('DOM.resolveNode',{backendNodeId:shadow.backendNodeId,executionContextId:world});
      await cdp.send('Runtime.callFunctionOn',{objectId:s.object.objectId,functionDeclaration:'function(host){globalThis.__pcsAutomationRoots.set(host,this);}',arguments:[{objectId:h.object.objectId}]});
      await cdp.send('Runtime.releaseObject',{objectId:h.object.objectId});
      await cdp.send('Runtime.releaseObject',{objectId:s.object.objectId});
    }
  }
  const timer=setInterval(()=>{if(!pending)pending=synchronize().catch(error=>{if(!closing&&!/context|document|session|target|frame|node|world/i.test(error.message))throw error;}).finally(()=>{pending=null;});},60);
  return async()=>{closing=true;clearInterval(timer);await pending;await cdp.detach();};
}
