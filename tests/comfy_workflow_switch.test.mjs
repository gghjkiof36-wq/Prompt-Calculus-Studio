import {test} from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {KEY} from '../comfyui_prompt_studio/web/state.js';

test('extension setup and graph-switch hooks restore desktop ownership without opening sidebar',async(t)=>{
    const intervals=[],originalInterval=globalThis.setInterval;
    globalThis.setInterval=(...args)=>{const id=originalInterval(...args);intervals.push(id);return id;};
    t.after(()=>{intervals.forEach(clearInterval);globalThis.setInterval=originalInterval;});
    const turn=()=>new Promise(r=>setImmediate(r));
    const n=id=>({id,title:'fixture '+id,inputs:[],widgets:[{name:'text',value:'landscape'}],properties:{[KEY]:{
        field:'text',snapshot:{final_prompt:'landscape',generated_prompt:'landscape',state:{draft:null},manual_draft:false}}}});
    const a=n(1),b=n(2); let extension,owner='',serial=0; const waiting=[];
    globalThis.document={createElement:()=>({}),head:{append(){}},addEventListener(){}};
    globalThis.testApp={graph:{_nodes:[a],extra:{prompt_studio_active:'1'},change(){},setDirtyCanvas(){}},
        extensionManager:{registerSidebarTab(){}},registerExtension:e=>extension=e,graphToPrompt:async()=>({})};
    globalThis.testApi={addEventListener(){},async fetchApi(url,options={}){
        const data=options.body?JSON.parse(options.body):null, route=url.split('/prompt_studio/')[1];
        let result={};
        if(route==='config') result={token:'fixture-token',settings:{destinations:{}}};
        else if(route==='library') result={state:{}};
        else if(route==='desktop/claim') {owner=data.target; result={lease:'lease'+(++serial)};}
        else if(route==='desktop/release') {owner=''; for(const resolve of waiting.splice(0))resolve(null);}
        else if(route==='desktop/wait') result=await new Promise(r=>waiting.push(r));
        else throw new Error(route);
        return {ok:true,json:async()=>result};
    }};
    let source=await readFile(new URL('../comfyui_prompt_studio/web/prompt_studio.js',import.meta.url),'utf8');
    source=source.replace("import {app} from '../../scripts/app.js';",'const app=globalThis.testApp;')
        .replace("import {api} from '../../scripts/api.js';",'const api=globalThis.testApi;')
        .replace("'./state.js'",JSON.stringify(new URL('../comfyui_prompt_studio/web/state.js',import.meta.url).href))
        .replace("'./desktop_binding.js'",JSON.stringify(new URL('../comfyui_prompt_studio/web/desktop_binding.js',import.meta.url).href))
        .replace("'./workflow_transfer.js'",JSON.stringify(new URL('../comfyui_prompt_studio/web/workflow_transfer.js',import.meta.url).href))
        .replace("'./node_images.js'",JSON.stringify(new URL('../comfyui_prompt_studio/web/node_images.js',import.meta.url).href))
        .replace("'./workflow_sync.js'",JSON.stringify(new URL('../comfyui_prompt_studio/web/workflow_sync.js',import.meta.url).href))
        .replace("new URL('./style.css', import.meta.url)","'http://fixture.invalid/style.css'");
    await import('data:text/javascript,'+encodeURIComponent(source)); await extension.setup();
    for(let i=0;i<12;i++) await turn(); assert.match(owner,/#1 \/ text/);
    globalThis.testApp.graph._nodes=[b]; globalThis.testApp.graph.extra={prompt_studio_active:'2'};
    extension.afterConfigureGraph(); for(let i=0;i<12;i++) await turn(); assert.match(owner,/#2 \/ text/);
    assert.equal(b.widgets[0].value,'landscape');
    globalThis.testApp.graph._nodes=[a]; globalThis.testApp.graph.extra={prompt_studio_active:'1'};
    extension.afterConfigureGraph(); for(let i=0;i<12;i++) await turn(); assert.match(owner,/#1 \/ text/);
    globalThis.testApp.graph.extra.prompt_studio_desktop=false; extension.afterConfigureGraph();
    for(let i=0;i<12;i++) await turn(); assert.equal(owner,''); assert.equal(serial,3);
    delete globalThis.document; delete globalThis.testApi; delete globalThis.testApp;
});
