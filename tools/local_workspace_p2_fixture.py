"""Prepare new, synthetic files for reviewing the actual Qt workspace."""
import argparse
import json
from pathlib import Path
import local_workspace_p0 as p0
from prompt_calculus_studio.local_documents import category, workflow, assets, io


def prepare(out):
    out = out.absolute()
    out.mkdir(parents=True, exist_ok=False)
    source = out/'source'
    source.mkdir()
    (source/'assets').mkdir()
    (source/'assets/p0-swatch.png').write_bytes(p0.png())
    root = out/'創作資料（合成）'
    root.mkdir()
    (root/'筆記').mkdir()
    (root/'參考').mkdir()
    state, ids = p0.fixture()
    documents = {
        '場景.pcs-category': category.from_state(state, ids['category_a']),
        '動作.pcs-category': category.from_state(state, ids['category_b']),
        '街景.pcs-workflow': workflow.from_state(state, ids['workspace_a']),
        '夜景.pcs-workflow': workflow.from_state(state, ids['workspace_b']),
    }
    for name, document in documents.items():
        document = assets.prepare(document, source, root)
        result = io.create(root, name, document)
        assert result.status == 'saved', result
    (root/'筆記/場景筆記.md').write_text('# 場景筆記\n\n這份內容是分頁檢視用的合成資料。\n\n- 晨光中的街道\n- 店面與遠景\n', encoding='utf-8')
    (root/'空白.md').write_bytes(b'')
    (out/'data').mkdir()
    print(json.dumps(dict(root=str(root), data=str(out/'data')), ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    prepare(parser.parse_args().out)
