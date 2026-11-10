# Baseline 8e9e87ee3b6f96e63ee4a000c2c109120b6a8681; positive guard control.
def rename(self):
    profile=self.selected()
    if not profile: return
    name,ok=InputDialog.getText(self.window,'工作流名稱','名稱',text=profile['name'])
    if ok and name.strip(): profile['name']=name.strip(); self.window.generation_panel.changed(); self.refresh()
