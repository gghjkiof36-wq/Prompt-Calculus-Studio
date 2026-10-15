"""Read-only type/category navigation shared by the library and its pickers."""
from . import material_navigation as nav, prompt_materials as pm


TYPE_SHORTCUTS = {None: 'text', '@material-type:combination': 'combination', '@material-type:flow': 'flow'}


class MaterialScope:
    def __init__(self, state, allowed=None):
        self.state=state;self.allowed=set(allowed or pm.TYPES)
        self.shortcuts={key:kind for key,kind in TYPE_SHORTCUTS.items() if kind in self.allowed}
        self.default=next(iter(self.shortcuts))
        self.categories={item['id']:item for item in state['modules']}
        view=nav.view(state);self.folders={item['id']:item for item in view['folders']}
        self.uncategorized=next((key for key,item in self.categories.items() if item['name']=='未分類'),'@uncategorized')
        self.collapsed=set(state['settings'].get('material_folder_collapsed',[]))
        counts=dict.fromkeys(self.categories,0);kinds={key:set() for key in self.categories}
        type_counts=dict.fromkeys(pm.TYPES,0)
        for item in state['items']:
            kind=pm.kind(item);key=item['module'];kinds.setdefault(key,set()).add(kind)
            if kind in self.allowed:
                counts[key]=counts.get(key,0)+1;type_counts[kind]+=1
        # Keep real category membership intact. Only redundant navigation rows
        # are aliases; a custom same-name category containing other types stays.
        self.aliases={key:shortcut for key,item in self.categories.items()
                      for shortcut,kind in TYPE_SHORTCUTS.items()
                      if kind!='text' and item['name']==pm.TYPES[kind] and kinds[key]<={kind}}
        visible={key for key in self.categories if key not in self.aliases and
                 (self.allowed==set(pm.TYPES) or bool(kinds[key]&self.allowed) or
                  (not kinds[key] and 'text' in self.allowed))}
        self.rows=[]
        def add(key,name,count,kind='category',parent=None,**extra):
            self.rows.append(dict(id=key,name=name,count=count,kind=kind,parent=parent,
                                  collapsed=key in self.collapsed,visible=parent not in self.collapsed,**extra))
        for key,kind in self.shortcuts.items():
            add(key,pm.TYPES[kind],type_counts[kind],'shortcut',fixed_top=True,
                divider_after=key==next(reversed(self.shortcuts)))
        for key in view['order']:
            if key in self.folders:
                folder=self.folders[key];children=[cid for cid in folder['categories'] if cid in visible]
                if not children and self.allowed!=set(pm.TYPES):continue
                add(key,folder['name'],sum(counts[cid] for cid in folder['categories']),'folder')
                for cid in children:add(cid,self.categories[cid]['name'],counts[cid],parent=key)
            elif key in visible:add(key,self.categories[key]['name'],counts[key])
        unclassified=sum(count for key,count in counts.items() if key==self.uncategorized or key not in self.categories)
        if 'text' in self.allowed or unclassified:
            add(self.uncategorized,'未分類',unclassified,'shortcut')
        self.keys={row['id'] for row in self.rows}

    def normalize(self, key):
        if key=='@uncategorized':key=self.uncategorized
        key=self.aliases.get(key,key)
        return key if key in self.keys else self.default

    def matches(self, item, key):
        kind=pm.kind(item)
        if kind not in self.allowed:return False
        if key in TYPE_SHORTCUTS:return kind==TYPE_SHORTCUTS[key]
        if key in self.folders:return item['module'] in self.folders[key]['categories']
        if key==self.uncategorized:return item['module']==key or item['module'] not in self.categories
        return item['module']==key

    def name(self, key):
        return next((row['name'] for row in self.rows if row['id']==key),pm.TYPES[self.shortcuts[self.default]])


def populate(scope, listing, combo, *, counts_in_combo=False):
    """Populate both responsive controls, retaining collapsed child choices."""
    from .material_folder_navigation import ROW
    listing.clear();combo.clear();listing.has_folders=bool(scope.folders)
    for data in scope.rows:
        key=data['id'];name=data['name'];parent=data['parent']
        caption=f'{scope.folders[parent]["name"]} / {name}' if parent else name
        combo.addItem(f'{caption} · {data["count"]}' if counts_in_combo else caption,key)
        if data['visible']:
            listing.add_row(key,name,data['count'],data['kind'],parent,data['collapsed'])
            entry=listing.item(listing.count()-1);entry.setData(ROW,{**entry.data(ROW),**data})
