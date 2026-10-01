// Frontend 1.43.18 provider references, captured at native widget creation.
// Late observation, a function's name and compiled source are not provenance.
export function captureWidgetAccess(widget) {
    let access=[];
    for(let owner=widget;owner;owner=Object.getPrototypeOf(owner)){
        const d=Object.getOwnPropertyDescriptor(owner,'value');
        if(d){access=[d.get,d.set,'value' in d,d.writable];break;}
    }
    return {callback:widget.callback,access,options:widget.options,
        getValue:widget.options?.getValue,setValue:widget.options?.setValue};
}
export function matchesWidgetAccess(widget,expected) {
    if(!expected)return false;
    const current=captureWidgetAccess(widget);
    return expected.callback===current.callback&&expected.options===current.options&&
        expected.access.length===current.access.length&&expected.access.every((v,i)=>v===current.access[i])&&
        expected.getValue===current.getValue&&expected.setValue===current.setValue;
}
export function createParameterCapabilities(app,widgets=null) {
    const serializers=new WeakMap(),primitives=new WeakMap(),factories={};let provider=null,original=null,wrapper=null;
    // Native primitive factories are independent of the backend node class.
    // An extension which changes callbacks/accessors after creation loses this
    // capability; matching a function name or inspecting its source is not proof.
    if(widgets)for(const name of ['INT','FLOAT','BOOLEAN','COMBO','STRING']){
        const factory=widgets[name];
        if(typeof factory!=='function'||factory.constructor?.name==='AsyncFunction')continue;
        const wrapped=function(node,...args){
            const result=factory.call(this,node,...args),widget=result?.widget;
            if(!result?.then&&widget&&(node.widgets??[]).includes(widget))primitives.set(widget,
                {name,wrapped,access:captureWidgetAccess(widget)});
            return result;
        };
        widgets[name]=wrapped;factories[name]=wrapped;
    }
    function install() {
        const candidates=(app.extensions??[]).filter(e=>e.name==='Comfy.DynamicPrompts');
        if(candidates.length!==1||typeof candidates[0].nodeCreated!=='function')return false;
        provider=candidates[0];original=provider.nodeCreated;
        if(original.constructor?.name==='AsyncFunction')return false;
        wrapper=function(node,...args){
            const before=new Map((node.widgets??[]).map(w=>[w,w.serializeValue]));
            const result=original.call(this,node,...args);
            if(!result?.then)for(const widget of node.widgets??[]){
                if(widget.dynamicPrompts===true&&widget.serializeValue!==before.get(widget)&&
                    typeof widget.serializeValue==='function'&&widget.serializeValue.constructor?.name!=='AsyncFunction')
                    serializers.set(widget,widget.serializeValue);
            }
            return result;
        };
        provider.nodeCreated=wrapper;return true;
    }
    function trusted(widget){return provider?.nodeCreated===wrapper&&serializers.get(widget)===widget.serializeValue&&primitive(widget);}
    function primitive(widget){
        const p=primitives.get(widget);
        return !!p&&widgets[p.name]===p.wrapped&&matchesWidgetAccess(widget,p.access);
    }
    function observeCallback(widget,changed){
        // PCS owns this notification wrapper. Preserve an already verified
        // native access path, but never promote an unobserved/modified widget.
        const verified=primitive(widget),previous=widget.callback;
        const callback=function(...args){const result=previous?.apply(this,args);changed();return result;};
        widget.callback=callback;
        if(verified)primitives.get(widget).access.callback=callback;
    }
    function reason(graph){
        for(const node of graph?._nodes??[]){
            if(node.subgraph||node.isVirtualNode)return '此工作流含尚未適配的子圖或動態節點，參數僅供查看';
            for(const widget of node.widgets??[])if(typeof widget.serializeValue==='function'&&!trusted(widget))
                return '此工作流含尚未適配的特殊序列化控制，參數僅供查看';
        }
        return '';
    }
    // The frontend caches core factories in a reactive widget registry. Its
    // official registration hook invalidates that cache before node creation;
    // replacing the plain ComfyWidgets properties alone does not.
    install();return {trusted,primitive,reason,observeCallback,getCustomWidgets:()=>({...factories})};
}
