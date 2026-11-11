"""Former builder/output refresh writes, owned by document boundaries."""


def prune(state):
    from .composition import prune_instances
    prune_instances(state)
    if state.get('uses'):state.setdefault('prompt_layout','paragraphs')


def normalize(state):
    from .multi_output import capture_current
    prune(state)
    if 'multi_output' in state:capture_current(state)


FIELDS=('instances','workspaces','version','prompt_layout')
