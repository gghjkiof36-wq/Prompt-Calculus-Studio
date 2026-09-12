"""Validate imported resource manifests before replacing any local records."""
def validate_resources(resources):
    if not isinstance(resources,list): raise ValueError("資源索引必須是清單。")
    ids=set(); albums=set()
    for row in resources:
        if not isinstance(row,dict) or row.get("kind") not in ("album","image","model","recent"): raise ValueError("資源類型無效。")
        for key in ("id","name","parent"):
            if not isinstance(row.get(key),str): raise ValueError("資源欄位格式無效。")
        if not row["id"] or row["id"] in ids: raise ValueError("資源 ID 重複。")
        ids.add(row["id"])
        body=row.get("body")
        if not isinstance(body,dict) or body.get("id")!=row["id"] or body.get("name")!=row["name"]: raise ValueError("資源內容無效。")
        if row["kind"]=="album": albums.add(row["id"])
        else:
            if not isinstance(body.get("path"),str): raise ValueError("缺少原檔路徑。")
            if not isinstance(body.get("thumb",""),str): raise ValueError("預覽路徑格式錯誤。")
        if row["kind"]=="image":
            if body.get("album")!=row["parent"] or not isinstance(body.get("metadata",{}),dict) or not isinstance(body.get("notes",""),str): raise ValueError("圖片資料無效。")
        if row['kind']=='recent':
            source=body.get('source')
            if not isinstance(source,dict) or not isinstance(source.get('prompt_id'),str) or not isinstance(source.get('image'),dict) or not isinstance(body.get('collected',{}),dict):
                raise ValueError('最近生成的來源格式無效。')
        if row["kind"]=="model":
            if body.get("root")!=row["parent"] or body.get("kind") not in ("LoRA","CKPT","Diffusion") or not isinstance(body.get("relative"),str): raise ValueError("模型索引無效。")
            if type(body.get("size")) is not int or type(body.get("mtime")) is not int: raise ValueError("模型檔案資訊無效。")
            for key in ("category","trigger","url","notes"):
                if not isinstance(body.get(key,""),str): raise ValueError("模型說明格式錯誤。")
    for row in resources:
        if row["kind"]=="image" and row["parent"] not in albums: raise ValueError("圖片引用不存在的資料夾。")
