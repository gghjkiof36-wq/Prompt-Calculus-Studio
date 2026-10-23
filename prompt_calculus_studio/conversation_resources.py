"""Copy only assets referenced by a fixed conversation subgraph."""
from .conversation_archive import Archive, Limits, decode_json
from .conversation_markdown import attachment_plan
from .local_documents import paths
from .retained_files import digest, read_local


def selected_resources(source, item, cancel=None):
    receipt_path = source.path.parent / '匯入完成.json'
    hashes = {}
    if receipt_path.is_file():
        receipt = decode_json(read_local(receipt_path, 8*1024*1024, cancel))
        hashes = receipt.get('attachments', {})
        if not isinstance(hashes,dict):raise ValueError('對話附件收據無法讀取。')
    retained = source.item.raw.get('pcs_extraction', {}).get('attachments', {})
    retained_hashes = source.item.raw.get('pcs_extraction', {}).get('asset_hashes', {})
    if not isinstance(retained,dict):raise ValueError('來源附件關係無法讀取。')
    archive = Archive(source.path, (), '', [item], tuple(hashes), Limits())
    mapping, assets, warnings = attachment_plan(archive, item, cancel)
    # Re-evaluate missing entries after applying a portable reference map.
    warnings = list(item.warnings)
    files, copied = {}, {}
    for reference in mapping:
        target = retained.get(reference, mapping[reference])
        mapping[reference] = target
        if not target:
            warnings.append('對話附件未取得：' + reference); continue
        try:
            path = paths.checked(source.path.parent, target)
            data = read_local(path, cancel=cancel)
            member = next((name for name, dest in assets.items() if dest == target), None)
            if member and digest(data) != hashes[member]:
                raise ValueError('對話附件內容已變更：' + target)
            if target in retained_hashes and digest(data) != retained_hashes[target]:
                raise ValueError('已保存附件內容已變更：' + target)
            files[target] = data
            copied[reference] = target
        except FileNotFoundError:
            warnings.append('對話附件缺失：' + target)
            mapping[reference] = None
    for target,data in files.items():
        if digest(read_local(paths.checked(source.path.parent,target),cancel=cancel))!=digest(data):
            raise ValueError('來源附件在讀取期間已變更：'+target)
    return mapping, files, copied, warnings
