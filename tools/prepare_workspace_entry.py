"""Arrange a fresh synthetic preview only; retain all document content."""
import argparse
import copy
import json
import os
from pathlib import Path


def prepare(root, data, screenshot):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    os.environ['PROMPT_STUDIO_DATA'] = str(data)
    from PySide6.QtGui import QFont, QFontDatabase
    from PySide6.QtWidgets import QApplication
    from prompt_calculus_studio.workspace_shell import WorkspaceShell

    if (data/'workspace-layout.json').exists():
        raise ValueError('Use fresh preview data; existing layouts are never reset.')
    app = QApplication([])
    for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
    app.setFont(QFont('Microsoft JhengHei UI', 12))
    w = WorkspaceShell(root, data)
    w.split_ratio = .75
    tab = w.open_path(root/'街景.pcs-workflow')
    w.open_path(root/'筆記/場景筆記.md', pane=w.ensure_split())
    w.focus_tab(tab)
    w.show()
    for _ in range(5):app.processEvents()
    w.pane_split.setSizes([750, 250])
    w.remember_ratio()
    for _ in range(3):app.processEvents()
    canvas = tab.host.canvas
    before = copy.deepcopy(tab.host.state)
    positions, containers = {}, {}
    gap = 72
    # The existing graph reads in lanes: canvas -> output -> bound input.
    links = canvas.data()['connections']
    output_order = list(canvas.outputs)
    def ordered(collection, upstream):
        keys = [link['destination'] for parent in upstream for link in links
                if link['source'] == parent and link['destination'] in collection]
        return {key: collection[key] for key in dict.fromkeys(keys + list(collection))}
    source_order = [link['source'] for output in output_order for link in links
                    if link['destination'] == output and link['source'] in canvas.containers]
    sources = {key: canvas.containers[key]
               for key in dict.fromkeys(source_order + list(canvas.containers))}
    clips = ordered(canvas.clips, output_order)
    x = 0
    clip_x, clip_bottom = 0, 0
    for collection in (sources, canvas.outputs, clips):
        y = 0
        width = 0
        for key, card in collection.items():
            width = max(width, card.width)
            if collection is sources:
                containers[key] = [x, y]
                dx, dy = x-card.x(), y-card.y()
                for member in canvas.data()['canvases'][key]['members']:
                    item = canvas.cards.get(member)
                    if item is not None:positions[member] = [item.x()+dx, item.y()+dy]
            else:positions[key] = [x, y]
            y += card.height + gap
        if collection is clips:clip_x, clip_bottom = x, y
        x += width + gap
    # Result panels follow the bound inputs, without a fourth distant column.
    x, y = clip_x, clip_bottom
    for collection in (canvas.flow_cards, canvas.preview_cards, canvas.functions.cards):
        for key, card in collection.items():
            if card.parentItem() is None and card.isVisible():
                positions[key] = [x, y]
                y += card.height + gap

    def arrange(state):
        state.setdefault('text_positions', {}).update(positions)
        for key, pos in containers.items():
            state['multi_output']['canvases'][key]['position'] = pos

    canvas.commit(arrange, label='整理入口布局')
    for _ in range(4):app.processEvents()
    assert w.save_tab(tab)
    canvas.fit()
    canvas._hidden_view = None
    tab.host.remember_canvas_view()
    for _ in range(3):app.processEvents()
    assert w.layout_store.save()
    screenshot.parent.mkdir(parents=True, exist_ok=True)
    assert w.grab().save(str(screenshot))
    # Compare semantic state: this command may move nodes, never edit them.
    after = copy.deepcopy(tab.host.state)
    old_history = before.pop('content_history', {}).get('entries', [])
    new_history = after.pop('content_history', {}).get('entries', [])
    assert new_history[:len(old_history)] == old_history
    assert all(not entry['content_changed'] and not entry['changes']
               for entry in new_history[len(old_history):])
    def content(state):
        for key in ('text_positions', 'canvas_view', 'workspace_scenes'):
            state.pop(key, None)
        for item in state['multi_output']['canvases'].values():
            item.pop('position', None)
        return state
    before, after = content(before), content(after)
    if before != after:
        (screenshot.parent/'state-before.json').write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding='utf-8')
        (screenshot.parent/'state-after.json').write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding='utf-8')
        raise AssertionError('Unexpected non-layout state change')
    result = dict(platform=app.platformName(), ratio=w.split_ratio,
                  nodes=len(positions)+len(containers), content_unchanged=True,
                  screenshot=str(screenshot))
    (screenshot.parent/'entry-layout.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    w.close()
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--screenshot', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.root.resolve(), args.data.resolve(), args.screenshot.resolve())
