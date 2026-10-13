"""Local, unapplied Stage editor drafts, separate from executable state."""
import json


def scope(workspace, server, stage, item=None):
    return json.dumps(['draft', workspace, server, stage, item], ensure_ascii=False)


def read(store, key):
    store.db.execute('CREATE TABLE IF NOT EXISTS stage_editor_local (key TEXT PRIMARY KEY, body TEXT NOT NULL)')
    row = store.db.execute('SELECT body FROM stage_editor_local WHERE key=?', (key,)).fetchone()
    return json.loads(row[0]) if row else None


def write(store, key, value):
    read(store, key)
    with store.db:
        if value is None:
            store.db.execute('DELETE FROM stage_editor_local WHERE key=?', (key,))
        else:
            store.db.execute('INSERT OR REPLACE INTO stage_editor_local VALUES (?,?)',
                             (key, json.dumps(value, ensure_ascii=False)))


def capture(sheet):
    return dict(version=1, workflow=sheet.workflow.currentData(),
        initial_stage=sheet.initial_stage, initial_workflow=sheet.initial_workflow,
        initial_config=sheet.initial_config, task_base=sheet.task_base,
        configs=sheet.configs, profiles=sheet.profiles,
        fields=[dict(workflow=flow, path=list(k[0]), field=k[1], raw=raw,
                     descriptor=sheet.draft_fields.get((flow,k)), mode=sheet.modes.get((flow,k)))
                for (flow,k),raw in sheet.raw.items()])


def restore(sheet, value):
    if not isinstance(value,dict) or value.get('version') != 1:return False
    sheet.initial_stage=value['initial_stage'];sheet.initial_workflow=value['initial_workflow']
    sheet.initial_config=value['initial_config'];sheet.task_base=value['task_base']
    sheet.configs=value['configs'];sheet.profiles=value['profiles']
    for entry in value['fields']:
        k=(entry['workflow'],(tuple(entry['path']),entry['field']))
        sheet.raw[k]=entry['raw']
        if entry.get('descriptor') is not None:sheet.draft_fields[k]=entry['descriptor']
        if entry.get('mode') is not None:sheet.modes[k]=entry['mode']
    sheet.restored_workflow=value['workflow']
    return True
