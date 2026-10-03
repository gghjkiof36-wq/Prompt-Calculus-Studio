// Offline execution of installed ComfyUI 1.53.6 sources. No browser or service.
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { pathToFileURL } from 'node:url';
import { createHash } from 'node:crypto';

export const assets = process.env.PCS_FRONTEND_ASSETS;
if (!assets) throw Error('PCS_FRONTEND_ASSETS must point to an existing frontend 1.53.6 static/assets directory');
const maps = ['settingStore-DDHzGrHr', 'core-C8yW9NC5', 'Popover-8b3JoYW2', 'formatUtil-grDslll6'];
const sources = maps.flatMap(name => {
    const map = JSON.parse(readFileSync(`${assets}/${name}.js.map`, 'utf8'));
    return map.sources.map((path, i) => ({ path, text: map.sourcesContent[i], map: `${name}.js.map` }));
});
const used = new Map();
export function nativeSource(suffix) {
    const found = sources.filter(source => source.path.endsWith(suffix));
    if (found.length !== 1) throw Error(`Expected one source for ${suffix}, got ${found.length}`);
    used.set(found[0].path, found[0]);
    return found[0].text;
}
export function sourceEvidence() {
    return [...used.values()].map(({ path, text, map }) => ({ path, map, sha256: createHash('sha256').update(text).digest('hex') }));
}
export const plain = source => stripTypeScriptTypes(source
    .replace(/^import[\s\S]*? from ['"][^'"]+['"]\s*$/gm, '')
    .replace(/^export \{[^}]+\}(?: from ['"][^'"]+['"])?\s*$/gm, '')
    .replace(/^import ['"][^'"]+['"]\s*$/gm, ''), { mode: 'transform' }).replace(/^export /gm, '');
const evaluate = (suffix, dependencies, returned) => Function(...Object.keys(dependencies), `${plain(nativeSource(suffix))};return ${returned};`)(...Object.values(dependencies));
const unsupported = () => { throw Error('Unexercised browser/asset branch is unavailable in offline harness'); };
const noop = () => {};
// These names are verified against this exact installed bundle's export table.
const productionVue = await import(pathToFileURL(`${assets}/vendor-vue-core-DwKKv_Jy.js`));
export const vue = { computed: productionVue.F, ref: productionVue.It, reactive: productionVue.Pt,
    toRaw: productionVue.Bt, toValue: productionVue.Ut, defineStore: productionVue.f, createPinia: productionVue.d };
for (const [name, fn] of Object.entries(vue)) if (typeof fn !== 'function') throw Error(`Missing real Vue/Pinia export: ${name}`);

export function createRuntime() {
    const pinia = vue.createPinia();
    const nodeIds = evaluate('/types/nodeId.ts', {}, '{toNodeId,parseNodeId,UNASSIGNED_NODE_ID}');
    const widgetIds = evaluate('/types/widgetId.ts', nodeIds, '{widgetId,parseWidgetId,isWidgetId,ensureUniqueWidgetNames}');
    const useValueStore = evaluate('/stores/widgetValueStore.ts', { ...vue, ...nodeIds, ...widgetIds }, 'useWidgetValueStore');
    const valueStore = useValueStore(pinia);
    const useWidgetValueStore = () => valueStore;
    const util = evaluate('/utils/widget.ts', { evaluateMathExpression: unsupported }, '{deriveWidgetRenderState,resolveNodeRootGraphId,getWidgetStep,formatNumericWidgetValue}');
    const common = { ...util, ...widgetIds, useWidgetValueStore, t: value => value,
        drawTextInArea: unsupported, cachedMeasureText: unsupported, Rectangle: unsupported,
        litegraph: () => ({}), LiteGraph: {}, evaluateInput: unsupported, warnDeprecated: noop };
    const BaseWidget = evaluate('/widgets/BaseWidget.ts', common, 'BaseWidget');
    const BaseSteppedWidget = evaluate('/widgets/BaseSteppedWidget.ts', { BaseWidget }, 'BaseSteppedWidget');
    const classes = { BaseWidget, BaseSteppedWidget };
    for (const name of ['NumberWidget', 'BooleanWidget', 'TextWidget', 'ComboWidget', 'LegacyWidget', 'ButtonWidget']) {
        classes[name] = evaluate(`/widgets/${name}.ts`, { ...common, ...classes }, name);
    }
    const types = evaluate('/utils/type.ts', { without: unsupported }, '{toClass,isNodeBindable}');
    const mapping = { ...classes, instantiateClass: types.toClass };
    for (const name of nativeSource('/widgets/widgetMap.ts').matchAll(/^import \{ (\w+) \} from '\.\//gm)) {
        if (!(name[1] in mapping)) mapping[name[1]] = unsupported;
    }
    const toConcreteWidget = evaluate('/widgets/widgetMap.ts', mapping, 'toConcreteWidget');
    const nodeSource = nativeSource('/LGraphNode.ts');
    const serializeStart = nodeSource.indexOf('function serialiseWidgetValues(');
    const serializeEnd = nodeSource.indexOf('\n}\n', serializeStart) + 2;
    const serializeWidgets = Function(plain(nodeSource.slice(serializeStart, serializeEnd)) + ';return serialiseWidgetValues;')();
    const methods = nodeSource.slice(nodeSource.indexOf('  addWidget<'), nodeSource.indexOf('  addTitleButton('));
    if (!methods.includes('addCustomWidget<')) throw Error('Native node method boundaries changed');
    const LGraphNode = Function('toConcreteWidget', 'isNodeBindable', 'useWidgetValueStore', 'UNASSIGNED_NODE_ID', 'zeroUuid',
        plain(`class LGraphNode {\n${methods}\n}`) + ';return LGraphNode;')(
        toConcreteWidget, types.isNodeBindable, useWidgetValueStore, nodeIds.UNASSIGNED_NODE_ID, 'offline-zero');
    const format = evaluate('/formatUtil.ts', {}, '{generateUUID,processDynamicPrompt}');
    const useChainCallback = evaluate('/useChainCallback.ts', {}, 'useChainCallback');
    // DOM rendering/registration is outside scope. The actual DOMWidget class,
    // own accessors, textarea factory and binding code execute unchanged.
    const dom = evaluate('/scripts/domWidget.ts', { _: {}, ...vue, ...classes, LGraphNode, LiteGraph: {},
        useChainCallback, useDomWidgetStore: () => ({ registerWidget: noop, unregisterWidget: noop }), ...format },
        '{DOMWidgetImpl,ComponentWidgetImpl,isDOMWidget,addWidget}');
    const app = { extensions: [], rootGraph: { id: 'offline-graph' }, canvas: { processMouseWheel: unsupported } };
    const settingStore = { get: () => undefined, addSetting: noop };
    const textarea = evaluate('/multilineTextarea.ts', { ...dom, app, useChainCallback, useWidgetValueStore,
        useSettingStore: () => settingStore, useDomWidgetStore: unsupported,
        document: { createElement: () => ({ dataset: {}, addEventListener: noop }) },
        forwardMiddleButtonToCanvas: noop }, '{createMultilineInputElement,bindMultilineTextareaWidget}');
    const isCombo = spec => Array.isArray(spec[0]) || spec[0] === 'COMBO';
    const migration = evaluate('/nodeDef/migration.ts', { isComboInputSpec: isCombo,
        isComboInputSpecV1: spec => Array.isArray(spec[0]), getComboSpecComboOptions: spec => spec[1]?.options ?? [] },
        '{transformInputSpecV1ToV2,transformInputSpecV2ToV1}');
    let widgets;
    const factoryDependencies = { ...vue, ...util, ...widgetIds, ...textarea, ...dom, app,
        useWidgetValueStore, useSettingStore: () => settingStore, defineDeprecatedProperty: noop,
        addValueControlWidget: (...args) => widgets.addValueControlWidget(...args),
        addValueControlWidgets: (...args) => widgets.addValueControlWidgets(...args),
        clamp: (value, min, max) => Math.max(min, Math.min(max, value)), ...migration,
        t: value => value, MultiSelectWidget: unsupported, assetService: { shouldUseAssetBrowser: () => false },
        createAssetWidget: unsupported, isCloud: false, useAssetsStore: unsupported,
        getMediaTypeFromFilename: unsupported, useRemoteWidget: unsupported };
    for (const type of ['Int', 'Float', 'Boolean', 'String', 'Combo']) factoryDependencies[`is${type}InputSpec`] = spec => spec.type === type.toUpperCase();
    const factories = {};
    for (const name of ['Int', 'Float', 'Boolean', 'String', 'Combo']) {
        factories[`use${name}Widget`] = evaluate(`/composables/use${name}Widget.ts`, factoryDependencies, `use${name}Widget`);
    }
    for (const match of nativeSource('/scripts/widgets.ts').matchAll(/^import \{ (use\w+Widget) \}/gm)) {
        if (!(match[1] in factories)) factories[match[1]] = () => unsupported;
    }
    widgets = evaluate('/scripts/widgets.ts', { ...factories, ...migration, t: value => value,
        isComboWidget: widget => widget.type === 'combo', useSettingStore: () => settingStore,
        dynamicWidgets: {}, IS_CONTROL_WIDGET: evaluate('/controlWidgetMarker.ts', {}, 'IS_CONTROL_WIDGET'),
        nextValueForLinkedTarget: unsupported }, '{ComfyWidgets,addValueControlWidget,addValueControlWidgets}');
    const useRegistry = evaluate('/stores/widgetStore.ts', { ...vue, ComfyWidgets: widgets.ComfyWidgets,
        getInputSpecType: spec => spec[0] }, 'useWidgetStore');
    const registry = useRegistry(pinia);
    const service = evaluate('/services/extensionService.ts', { app, useWidgetStore: () => registry,
        useExtensionStore: () => ({ registerExtension: extension => app.extensions.push(extension) }),
        useSettingStore: () => settingStore, useKeybindingStore: () => ({ addDefaultKeybinding: noop }),
        useErrorHandling: () => ({ wrapWithErrorHandling: f => f, wrapWithErrorHandlingAsync: f => f, toastErrorHandler: noop }),
        useCommandStore: () => ({ loadExtensionCommands: noop }), useMenuItemStore: () => ({ loadExtensionMenuCommands: noop }),
        useBottomPanelStore: () => ({ registerExtensionBottomPanelTabs: noop }) }, 'useExtensionService()');
    evaluate('/dynamicPrompts.ts', { useExtensionService: () => service, processDynamicPrompt: format.processDynamicPrompt }, 'undefined');
    return { app, widgets: widgets.ComfyWidgets, registry, service, valueStore, classes, serializeWidgets,
        node(type) { const node = new LGraphNode(); Object.assign(node, {
            id: nodeIds.UNASSIGNED_NODE_ID, type, comfyClass: type, title: type, widgets: [], inputs: [], mode: 0,
            expandToFitContent: noop, isSubgraphNode: () => false, getInnerNodes: () => [],
        }); return node; },
        addToGraph(node, id) { node.id = String(id); node.graph = { rootGraph: app.rootGraph };
            for (const widget of node.widgets) if (types.isNodeBindable(widget)) widget.setNodeId(node.id);
            node.onAdded?.();
        },
    };
}
