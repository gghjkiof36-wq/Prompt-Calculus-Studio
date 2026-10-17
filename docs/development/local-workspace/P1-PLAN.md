# P1：可攜文件核心實作順序

狀態：需求主責已核對 P0 並明確交辦 P1-01～P1-09；完成 P1 後停於檢查點，P2 尚未放行。依 [ADR-001](ADR-001.md)；本階段不接 live 任務、不改 UI、不批量搬使用者資料。

## 依序完成的工程單位

| 順序 | 具體產物與預計檔案 | 必須證明 |
| --- | --- | --- |
| P1-01 | `local_documents/schema.py`：envelope、typed errors、LoadReceipt、版本／必要欄位；新測試 `test_local_document_schema.py` | file schema 與 legacy state version 分離；未知／損毀／重複 ID 不改原 bytes；已接受 optional extensions 不丟失。 |
| P1-02 | `local_documents/category.py`：分類 owned records、完整素材樹、驗證 wrapper；`test_category_documents.py` | 文字／組合／素材流程、多變體、nested copy／跨分類 provenance、順序、pin／notes 保留；沒有第二個 writable original。 |
| P1-03 | `local_documents/workflow.py`：限定 workspace 投影、profiles／舊 list 的必要依賴；`test_workflow_documents.py` | 同一檔保存兩內部 Canvas、connections、完整有效 output_order、bindings、各類空稿、游標、未連線節點；排除別的 workspace／分類哨兵。 |
| P1-04 | `local_documents/identity.py`：文件身分、來源定位、獨立副本映射與索引碰撞報告 | 改名不改 ID；獨立副本不覆寫原件；group／node／output／binding／cursor references 一致；外部同 ID 雙路徑不能註冊成兩個 writable owner。 |
| P1-05 | `local_documents/assets.py`：已知附件欄位、manifest、相對根解析與 hash | 本地合成圖隨整夾搬移可用；缺失／逃逸／不支持必要資源明確列出；不讀未接入根、憑證或私人絕對路徑。 |
| P1-06 | `local_documents/io.py`：read／create／save、raw-hash precondition、安全替換與回復結果；必要 Windows helper 分檔 | 同名不覆蓋、失敗保原件與 dirty model；外部修改及競態不靜默丟版本；失敗注入含寫 temp、flush、replace 前後。 |
| P1-07 | `local_documents/index.py`：只索引接入根的入口 metadata，按需 parse，重建與重複 ID 診斷 | 刪新索引後重建仍讀完整文件；無原 DB／原路徑可讀內容；不掃 `.obsidian`／目錄迴圈。 |
| P1-08 | 綜合合成 fixture／固定舊 fixture，必要共用 helper 置 `tests/` | 從現有狀態編碼 → 關讀取器 → 搬新根 → 解碼 → 核對語義。固定舊 cursor／Stage 保持各自原限制，不泛稱全部舊資料遷移。 |
| P1-09 | schema／欄位責任說明及 CHECKPOINT-P1 | 固定 commit、精確 W03～W07/W28 狀態、失敗修正前後證據、原件／回復方式。 |

以上名稱是工程拆分預設；小幅合併檔案由實作主責決定。不能為了配合名稱改資料語義或以簡化 title/body 達成測試。

## 可重用與不可直接套用

- 重用 `core.validate_state`、`state_loading.prepare_state`、`composition`／`prompt_materials` 驗證與遷移，`workspace_scene` 的 fields 定義、`multi_output` 的 ownership／draft／binding 規則。
- 只在顯式載入邊界轉換副本；encode 不更動呼叫端 state。保存所有屬於文件的內容，不能套 selected-only 的生成裁剪。
- `snapshots.portable_state`／restore_snapshot 具有 PNG 投影及新圖片工作區語義，不能直接當完整文件 codec。`comp.clone_node` 只是節點複製，不是完整工作流 ID remapper。
- P1 模組維持 Qt-free，但暫不加入擴充 `SHARED_MODULES`；檔案管理是桌面文件責任，生成配套沿既有資料格式。若必要修改現有 shared 模組，另跑 Qt-blocked import 與同版影響驗證。
- 不以空白測試根的成功代替跨分類依賴、雙輸出空稿、附件搬移或 revision 競態的證據。

## 起始資料與對照方式

先執行 `python tools/local_workspace_p0.py --out <新的隔離目錄>`。其中 `documents/legacy-state-v7.json` 是現有格式的合成來源，並非 `.pcs-category/.pcs-workflow`。identity-map 指向分類 A/B、跨分類原件、素材流程、兩工作區、Canvas／outputs、Reader；output/inventory.json 記 P0 已驗的關係。

P1 fixture 至少覆蓋：

1. 分類 A 擁有文字、組合與流程；組合內保留從 B 取得的舊內容，修改 B 原件不改 A 的嵌入副本；未用分類 C 的哨兵不外帶。
2. 工作流 A 有兩張內部 Canvas、不同輸出及空稿／非空稿／list 空稿、兩個不同綁定、非視覺順序的 output_order、Reader repeat=1；B 有相同局部節點 ID 但不同草稿／鏡頭。
3. 類型、巢狀樹、variant／group ID、起始／目前位置、已存在的序列進度逐欄核對；不只核對數量或最終文字。
4. 只給分類／工作流檔與相對附件的新根，移除測試索引、關閉所有 reader 後重開；解碼不建立任務／journal。
5. 新版／破 JSON／缺資產／身分衝突／路徑逃逸／大小寫重名／保留名稱／寫入與替換競態的負例；候選 model 和競爭原件有保留證據。

## 驗證與出關

從 checkout 根用 unittest discover 跑對應檔案；Qt-free fixture／codec 的程序顯式阻擋 PySide6／shiboken import。保存故障及未知版本須比 bytes／hash、dirty model、錯誤類型；內容須比完整受支持語義，不以刪欄把等式湊成立。

通過本階段受影響檢查後只因新改動、失敗或未解問題再擴大測試。P1 不跑真 ComfyUI／GPU，也不把原生 UI 或全庫回歸當本階段假成果。

出關最小成果是一份完整分類及一份完整工作流，能在沒有原 DB 與原路徑的另一根重開；資料、附件及失敗保護有可重現證據。依規格 5.4，一般替換前失敗保持原路徑；替換時發現競態則完整保留外部與候選回復檔，明確回傳衝突且候選仍 dirty，不自動重試。此結果與一般保存成功分開記錄，不宣稱完整 CAS。CHECKPOINT-P1 通過需求核對後才進 P2。

## 後續階段接點（僅責任，不提前實作）

- P2：文件 registry／context host、Qt頂層分頁及完整中央素材管理；對 `window.py`、`studio_navigation.py`、`context_rail.py`、`prompt_editor.py`／素材 editor 的入口適配。
- P3：檔案命令與平台 recycle／move、改名、複製、樹序；中央保留素材的分類解散與側欄文件回收分開。
- P4：`text_canvas.py`／`multi_canvas.py` 的 context、原生 editor documents、明確 Canvas 目標與 RunControls；既有任務引擎只適配歸屬／跨介質保存，不重寫規則。
- P5：普通 MD 原生 document／工具／預覽與監看衝突。
- P6：舊資料 dry-run、身分／歷史對照、整合與獨立驗證、隔離候選。原前置啟動器與使用者資料保持。
