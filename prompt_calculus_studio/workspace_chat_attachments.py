"""Qt-free, bounded text snapshots of saved PCS documents for an attachment worker."""
import json
from pathlib import Path

from .local_documents import paths, schema
from .retained_files import read_local

MAX_BYTES = 8 * 1024 * 1024
MAX_TEXT = 200_000


class _Text:
    def __init__(self):
        self.parts, self.size = [], 0

    def add(self, value):
        self.size += len(value) + 2
        if self.size > MAX_TEXT:
            raise ValueError('文件文字超過 200,000 字，請選擇較小的文件。')
        self.parts.append(value)

    def finish(self):
        return '\n\n'.join(self.parts)


def _material_text(output, item):
    from . import composition, prompt_materials
    root = composition.prototype(item)
    output.add('### ' + item['name'] + ' · ' + prompt_materials.TYPES[prompt_materials.kind(item)])
    sequence = root.get('sequence') or root.get('variants')
    if sequence:
        field = 'sequence' if root.get('sequence') else 'variants'
        # Detached projection of each saved group, matching the existing content
        # history preview. Never advance a cursor or mutate the saved owner.
        for group in sequence['groups']:
            projection = dict(root, **{field: dict(sequence, selected=group['id'])})
            output.add(group['name'] + f" · {group['repeat']} 次\n" + composition.render(projection))
    else:
        output.add(composition.render(root))
    if item.get('notes'):
        output.add('備註：' + item['notes'])


def _native_text(document, title):
    payload, kind = document['payload'], document['kind']
    if kind == 'chat':
        from .local_documents.chat import markdown
        text = markdown(payload, title)
        if len(text) > MAX_TEXT:
            raise ValueError('對話文字超過 200,000 字，請選擇較小的文件。')
        return text
    output = _Text()
    output.add('# ' + title)
    if kind == 'category':
        output.add('## 素材 · ' + payload['category']['name'])
        for item in payload['items']:
            _material_text(output, item)
    elif kind == 'workflow':
        from . import core, multi_output
        state = payload['state']
        output.add('## Canvas · ' + state['workspaces'][0]['name'])
        for value in state.get('canvas_functions', {}).get('text_sources', {}).values():
            _material_text(output, dict(name=value['name'], composition=value['root']))
        if state.get('multi_output'):
            for key, value in state['multi_output']['outputs'].items():
                try:
                    text = multi_output.compile_output(state, key)['final_prompt']
                except ValueError as error:
                    text = '（此輸出尚未就緒：' + str(error) + '）'
                output.add('### ' + value['name'] + '\n' + text)
        else:
            text = state.get('draft')
            output.add('### Prompt\n' + (text if text is not None else core.build_prompt(state)))
        if state['items']:
            output.add('## 引用素材')
            for item in state['items']:
                _material_text(output, item)
    else:
        raise ValueError('不支援這種 PCS 文件。')
    return output.finish()


def prepare_pcs_attachment(path, workspace_root):
    """Read saved bytes only; do not open editors, follow assets, save, or execute."""
    root = paths.root_path(workspace_root)
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(root).as_posix()
    except ValueError:
        raise ValueError('請從目前加入的工作區資料夾選擇 PCS 文件。') from None
    if any(part.casefold() in ('.pcs', '.obsidian', 'assets') for part in candidate.relative_to(root).parts):
        raise ValueError('請選擇文件，不要選擇程式資料或附件資料夾。')
    target = paths.checked(root, relative)
    suffix = target.suffix.casefold()
    if suffix not in ('.md', *schema.SUFFIXES.values()):
        raise ValueError('支援 Markdown、Manager、Canvas 與 PCS 對話文件。')
    raw = read_local(target, MAX_BYTES)
    if suffix == '.md':
        text = raw.decode('utf-8-sig')
        if '\x00' in text:
            raise ValueError('這不是 UTF-8 文字文件。')
        if text.lstrip().startswith('{'):
            try:
                value = json.loads(text)
            except ValueError:
                value = None
            if isinstance(value, dict) and value.get('format') == 'pcs-document':
                raise ValueError('這是 PCS 文件，但副檔名是 Markdown；請先確認原文件種類。')
        label = 'Markdown'
    else:
        document = schema.decode(raw)
        if schema.SUFFIXES[document['kind']] != suffix:
            raise ValueError('文件種類與副檔名不一致，未加入附件。')
        text = _native_text(document, target.stem)
        label = {'category': 'Manager', 'workflow': 'Canvas', 'chat': 'PCS 對話'}[document['kind']]
    if len(text) > MAX_TEXT:
        raise ValueError('文件文字超過 200,000 字，請選擇較小的文件。')
    return dict(name=target.name, kind='text', text=text,
                source=(label + ' · ' + target.name + ' · 已保存快照')[:256])
