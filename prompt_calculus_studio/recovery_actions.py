"""Shared, item-scoped exit for missing generation jobs (GUI entry points)."""

LABEL = '停止追蹤此筆（保留紀錄）'


def add_actions(menu, generation, **scope):
    ids=generation.abandonable(**scope)
    for ident in ids:
        title=LABEL if len(ids)==1 else LABEL+' · '+ident[:8]
        menu.addAction(title,lambda checked=False,key=ident:generation.abandon(key))
