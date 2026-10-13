# 0.8.7 AI 與 skill 接入契約

本文件定義 F01–F04 的後續接入方式。0.8.7 本輪只交付規劃，不執行模型、不加入無作用的按鈕，也不宣稱轉換品質。
AI 回傳的是可編輯建議；使用者採用後，才成為一般文字卡片或素材。保留原文、獨立副本與可復原操作是共同前提。

## 既有接點與待新增項目

| 既有 API／資料 | 接入方式 |
| --- | --- |
| [materials.py](../../prompt_calculus_studio/materials.py)：`save`、`edit_canvas`、`from_canvas`、`add_to_canvas` | 保存素材、修改指定副本、另存素材、建立新的畫布副本；helper 自身不提交交易。 |
| `content_kind = tags / text / markdown` | tags 保留權重語義；自然語言採 text；Markdown 保留標記及換行，不套用 tags 清理。 |
| `preview`、`source_id`、`source_revision` | 沿用手動自訂預覽；素材加入畫布時保存來源 fingerprint，副本不隨原件更新。 |
| [image_source.py](../../prompt_calculus_studio/image_source.py)：`metadata_sources` | 取得正面／負面／未判定的原文及 choice；分類建議不另猜極性。 |
| [text_canvas.py](../../prompt_calculus_studio/text_canvas.py)：`commit` | 全部接受項目用一次操作提交，驗證後同步畫布並產生一次 Undo。 |
| [content_history.py](../../prompt_calculus_studio/content_history.py)：`record`、`entries`、`restore` | 內容版本、歷史查閱及透過新交易還原；不回捲生成紀錄。 |

模型 adapter、候選暫存、取消 token、以下 request/result schema 與 skill bundle 都是待實作契約，不能當作現有 API 呼叫。
未來若保存 `ai_provenance` 等資料，須先補版本化驗證、clone、匯入匯出、歷史及往返保存測試，不能只在 UI 掛欄位。

## 共用 request／result

| 欄位 | 約定 |
| --- | --- |
| `schema_version`, `request_id`, `operation` | 第一版 schema；唯一請求；明確操作種類，回覆必須原樣帶回。 |
| `scope` | workspace ID、canvas ID、use ID、nested node ID；素材原件另用 material ID。來源與套用目的分開保存。 |
| `sources[]` | 每筆含 `source_id`、原始 `text`、`text_sha256`、`polarity`、`content_kind` 及來源識別。 |
| `text_sha256` | 對未清理原文的 UTF-8 bytes 做 SHA-256；不 trim、不改換行、不做 Unicode normalization。拒絕無效 Unicode。 |
| `source_revision`, `snapshot_revision` | 前者沿用來源素材 fingerprint 作追溯；後者另外標識請求時完整內容快照。來源版本不代表副本目前內容，也不等於原文 hash。 |
| `output_version` | 呼叫端凍結的目的內容版本 token；版本須涵蓋當次草稿與選取來源，不能用顯示名稱或時間取代。 |
| `offset_unit` | 固定 `unicode_scalar`：0 起算、左閉右開 `[start,end)`，依來源原文計數；CRLF 算兩個字元。 |
| `options` | 目的語言、選取範圍及使用者給定語境；不得偷偷擴大到整個工作區。 |
| `result` | 帶回所有 scope／版本 token、候選、疑義及來源 spans；錯誤也用結構化狀態。 |

Qt 游標採 UTF-16 計數，adapter 必須轉換；不得把 UTF-16 offset 當 Unicode scalar offset。所有 spans 都要驗證界線及原文 hash。
對輸出候選的範圍另帶 `candidate_version`、`candidate_sha256`；修改、合併或拆分後重新計算，舊 offset 不再適用。
圖片來源保留 image SHA256、metadata key、node／field 及 `choice=[role,node,field]`；文件來源保留檔案 hash 與相對引用。
`polarity` 僅為 `positive / negative / unknown`。未知保持未知，同文字出現在正負兩處仍是兩筆來源。

## F01：文字轉換模塊

- `zh_tags_to_en_tags`：輸入中文 tags 原文，輸出英文 tags 候選，`content_kind=tags`；保存逐段來源對照與無法確定的詞。
- `tags_to_en_text`：輸入 tags 及可選語境，輸出英文自然語言候選，`content_kind=text`；人物、關係、動作與構圖疑義列為待確認。
- 英文自然語言 → tags 暫停，不登錄為可執行 operation，不透過「自動判斷方向」繞過。
- 原文字、權重括號、換行與未知語法保留在 source；候選可改寫，但不能把整理後文字冒充原文。
- tags 常缺人物指涉及空間關係；不得把補出的自然語言敘述宣稱為原文的完整等價形式。
- 結果預覽並列原文與候選，可逐項採用、修改或拒絕；未採用時不改 Prompt、卡片、素材或綁定。
- 採用目的需明確：替換指定副本文字、加入新副本，或保存新素材。替換素材原件需使用者選定原件範圍。

## F02：metadata 分組成卡片

1. 從 `metadata_sources` 選取一或多筆來源，保留正面、負面、未判定標記與原文；不先混成一個 Prompt。
2. 回傳候選群組：`group_id`、名稱、建議分類、內容種類、候選文字、`source_spans[]`、保留的極性集合及分類理由。
3. 審閱區使用既有手動卡片編輯能力；使用者可合併、拆分、改名、改字、調整分類及自訂預覽圖。
4. 合併後仍保存每段原始來源與極性；跨正負合併要明示，不能把負面默默改成正面，亦不建立自動執行綁定。
5. 未分類片段留在未判定群組供選擇；被省略的原文須能看到，不可無聲丟棄。
6. 使用者選定接受群組與目的後，一次交易產生本次畫布的獨立副本，或保存成素材；可同次保存並加入畫布。

建立畫布副本沿用 clone／assign；保存素材沿用 `materials.save`，素材再加入畫布走 `add_to_canvas`。
`preview` 是使用者選用的本機圖像，不自動用來源 PNG 覆寫既有預覽；原始圖片與來源紀錄保留。
來源關聯只作追溯，不讓後來 metadata 重讀、素材修改或模型回覆自動改動既有副本。

## F03：語境衝突檢查模塊

- 輸入帶人物／物件分組、上下文及極性的 tags；語境未提供就回傳不確定，不把詞對直接視為必然衝突。
- 每項建議包含 `issue_id`、來源 spans、對象、疑似衝突種類、理由、需確認的語境與可逆修改候選。
- 眼睛／眼罩、背部／胸部、光頭／頭髮可能分屬不同人物、視角或正負提示；先指出條件，再提供選擇。
- 建議動作可為保留、改寫、拆分人物群組或停用選定片段；不自動刪詞、不更改未選內容、不寫入永久排除規則。
- 使用者採用才修改目標副本；原文與 before snapshot 保留。一次採用多項仍是一筆可 Undo 的交易。
- 若停用需要目前資料模型未支持的片段粒度，先提出可審閱的拆分結果，不能將整張卡片意外停用。

## F04：長文字、Markdown 與 skill 保存／引用

- 現有 `text`／`markdown` 可承載長文和複雜提示詞；原始縮排、標記、換行及手動空稿的語義不得被 tags formatter 改寫。
- skill 暫不加入現有 `CONTENT_KINDS`。規劃為有版本的 bundle：manifest、入口 Markdown、相依檔清單及逐檔 SHA256。
- manifest 定義 bundle ID／revision、入口相對路徑、媒體型別、編碼、大小、相依相對路徑與可選說明；保存完整相依檔。
- 引用固定 `bundle_id + revision + relative_path + sha256`，不能只保存可能被覆寫的絕對路徑；改版產生新 revision。
- 匯入與匯出保存相對目錄結構；拒絕越出 bundle 的 `..`、絕對路徑或符號連結跳脫。缺檔／hash 不符明示且不套用。
- 文字素材可保存 skill 入口摘要與引用；畫布取用時建立獨立文字副本並記錄來源 revision，使用者另外決定是否更新。
- 新 revision 不回寫舊副本；來源移除不刪除已加入的文字。若只保留引用，缺失時可讀已保存快照並顯示來源不可用。
- 保存／顯示 Markdown 或相依腳本不等於執行授權；本契約不啟動程式、下載依賴、呼叫工具或建立完整 agent 平台。

## 執行生命週期與套用交易

1. 開始前完成未保存 Editor 草稿的正常處理，凍結原文、scope、source／snapshot revision、output version；產生取消 token。
2. 背景處理只寫候選暫存；UI 可取消。超時、解析失敗、拒絕或部分失敗保留來源與可讀錯誤，不自動重送或部分套用。
3. 回覆先核對 request、schema、取消狀態、workspace/use 是否存在、原文 hash、revision 與 output version。
4. 切工作區、刪除或編輯目標後的晚到結果只標記過期；不重新建立被刪卡片、不套到同名對象、不沿用失效 offsets。
5. 使用者審閱接受後，在交易內再次比對相同條件，再用既有 helper 對副本修改；任何驗證失敗都保留原狀。
6. 單次採用呼叫一次 `canvas.commit(..., label='採用文字建議', merge_key=None)`，避免與相鄰 Editor 打字合併；一次 Undo 還原整批。
7. `content_history.record` 記錄實際內容變更；查看、取消及失敗不冒充內容版本。歷史還原也走新交易，生成／排程紀錄不回捲。

## 後續實作最低驗收

使用合成 adapter 驗證 emoji／中文 offsets、空負面、多路 metadata、混合極性、未知語境、部分接受及一次 Undo。
驗證更換工作區／修改原文／刪除目標／重複回覆／超時取消後，晚到回覆均不自動套用；明確確認後方可重新請求。
驗證保存與重開保留原文、provenance、自訂預覽、Markdown 與 bundle 相依檔；原件修改不連動副本，舊資料仍可讀。
模型品質需另訂代表性語料與人工評估；契約符合、資料測試通過或示意候選，都不能當成語義品質已驗證。
