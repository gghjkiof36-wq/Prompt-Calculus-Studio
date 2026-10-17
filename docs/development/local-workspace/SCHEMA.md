# 可攜文件核心 v1

此核心保存分類與單一 workspace 的完整可編輯內容，供後續 Qt 文件 context 使用。尚未接入產品入口、任務引擎或舊資料導入。[ADR-001](ADR-001.md) 說明責任與階段邊界；實作在 [local_documents](../../../prompt_calculus_studio/local_documents/__init__.py)。

## 封裝

`.pcs-category` 與 `.pcs-workflow` 都是 UTF-8 JSON；讀取接受 UTF-8 BOM，寫入使用無 BOM、縮排 JSON。最大 32 MiB、256 層 JSON、100 萬個值；素材樹仍另受既有 32 層／5000 節點契約限制。重複 JSON key、循環、非有限數值及非 JSON 型別被拒絕，不截短內容。

| 欄位 | 語義 |
| --- | --- |
| format | 固定 `pcs-document` |
| file_schema_version | `1`，與 payload 的舊 state version 1～7、PNG schema 分開 |
| kind | `category` 或 `workflow`，必須與副檔名一致 |
| document_id | 標準 UUID，改名不變；明確獨立副本才換身分 |
| revision | UUID `id` 與前次 UUID `parent`；不是時間戳或全域序號 |
| payload | 已驗證的完整擁有內容及明確的唯讀依賴 |
| assets | logical reference → 相對實檔、SHA256、bytes、media type |
| extensions | namespaced 可選不透明 JSON，完整往返保留；不能暗自新增必要 ID 關係或執行語義 |
| required_features | v1 支援空陣列；非空表示需要未支援語義，拒絕保存 |

未知必要語義、外層／payload 封裝欄位與版本均明確拒絕。既有素材及場景 record 的可選欄位不作 PNG 式刪欄投影，通過原驗證器的內容完整深複製。新增真正有行為的格式需升版或增加明確支援的 feature，不能藏在不透明 extension。

## 分類、workspace 與本機責任

分類 payload 是 `state_version`、完整 `category` record、有序 `items`、`settings`。items 的陣列順序就是素材手動順序。原件的 pin、notes、aliases、preview、完整 composition／variants／sequence、inactive 內容與合法游標保持；驗證 wrapper 只為沿用核心，不是另一份儲存來源。

工作流 payload 是 `state`、`catalog_role="pinned_dependency"`、`profile_role="pinned_dependency"`。state 只含一個 workspace；保留全圖、所有內部 Canvas、輸出、未連線節點、接線、綁定、各種草稿、有效 output_order、Reader 進度及版面。只擷取仍有實際 list／fixed／instance／weight 等參照的分類及原件定義；內嵌副本的 source_id 不會擴張依賴閉包。

所有被選取、綁定、排序或 Stage／舊 chain 引用的 ComfyUI profiles 隨檔保存，包含完整圖和未綁定欄位；無關 workspace、分類與 profile 不隨檔。這些 profiles 是目標定義的唯讀快照，並未註冊成第二個可寫服務身分。

| 資料 | 責任 |
| --- | --- |
| formatter、artist_prefix、separate_selections、comfy_count | 文件內容；原先缺欄仍保持缺欄，不用新預設改舊 fallback |
| UI／連線／模型根目錄／字典與其他已知本機設定 | 保留在原本機來源；不整包複製到文件 |
| 未分類設定或 state 頂層欄位 | 停止匯出並列出欄位，避免靜默漏存 |
| content_history、workspace.history | 留在正式本機保存；`policy.local_retention` 明確交還此責任，函式本身不遷移或寫入 DB |
| journal、usage、accepted、任務、回執 | 原機制不改；文件開啟／複製不讀 DB、不建立／送出／接續任務 |
| workspace_scenes 活動重複快取、執行衍生值 | 不作第二份文件真相；後續 adapter 如需場景 cache，依該文件內容建立 |

P1 沒有接入新的內容歷史持久化或跨介質交易。匯出輸入不變，本機歷史原件仍在；不能據此宣稱只憑文件可以攜走完整執行會話。舊格式需要升級時，先在顯式載入邊界呼叫既有 `state_loading.prepare_state`，codec 不自行重編 Prompt 或消耗游標。

## 身分與獨立副本

`identity.duplicate` 回傳新文件及分 scope 的 ID mapping。分類原件、workspace、Canvas／use／output／clip／Stage／Reader／preview／scheduler port、素材節點與群組、舊 chain 階段及內部參照對應新身分。有效 selection／supply cursor 的群組及簽章隨擁有內容更新，repeat／draws 不消耗；原先失效的簽章不會被補成有效。

ComfyUI profile／graph node／server／frontend／target field、Stage 外部輸出與參數身分保留；不因與 PCS 節點碰巧同名就重綁。profile 在 P0 映射中屬共享 catalog，P1 明確按 pinned dependency 處理，只有 PCS 文件及場景擁有內容需要新 ID。來源 provenance 同樣保留出處，不改成副本自己的原件。

`identity.provenance` 回報 `owned-record`、`pinned-dependency` 或 `unresolved-provenance`，不偷讀其他根；來源不可得仍可讀內嵌內容。Registry 對實體檔案別名回報 same-file，對另一實體路徑的同 document_id 回報 duplicate-id，不能讓兩份同身分文件同時成為可寫 owner。

## 附件解析

manifest 每項包含 `reference`、`path`、`sha256`、`size`、`media_type`。實體檔在 `assets/<document-id>/<sha256>.<suffix>`，以 hash 命名、排他建立，不覆蓋既有不同內容。單檔上限 25 MiB，每次附件準備合計上限 256 MiB，超過即報錯，不減少內容。

既有 shared validator 要求圖片來源 `relative` 保持 `originals/generation/...`。因此 `reference` 是 payload 內的邏輯相對名稱，`path` 是文件目錄下的實體相對名稱；兩者由 manifest 明確對應。這樣完整資料與游標簽章都不因搬附件改變。**後續 context 的圖片／預覽讀取須呼叫 `assets.resolve(document, document_directory, reference)`，不能直接將舊 logical reference 接到文件目錄。**

`assets.prepare` 只從明確指定的來源根讀已知 item／node preview、generation source、圖片節點 source、Canvas image layer，驗證完成後才發佈附件。`inspect` 列缺失／hash 不符，`resolve` 每次核對邊界與內容。圖片模型名稱與服務可用性在 `external_dependencies` 列為 unverified；不下載或尋找替代來源。未知絕對資源欄位列出位置並阻擋可攜保存。

根與相對路徑拒絕絕對／父目錄逃逸、Windows 保留名、尾點／空白、ADS、重解析點及 symlink；`.obsidian` 既不掃描也不能當寫入根。索引只含 metadata，可刪除重建；`scan` 是不 parse 文件內容的 iterator，`inspect_entry` 才讀文件。

## 保存與回復 API

```python
loaded = io.load(root, "場景.pcs-category")
candidate = copy.deepcopy(loaded.document)
candidate["payload"]["items"][0]["notes"] = "修改後的備註"
result = io.save(root, "場景.pcs-category", candidate, loaded.receipt)
if result.status == "saved":
    document, receipt = result.document, result.receipt
else:
    # 保持 candidate 及 dirty；顯示 error 和 recovery_paths，不能自動重試覆蓋。
    pass
```

`LoadReceipt` 記當次 raw SHA256、實體檔案識別、document_id 與 revision；它不被寫入文件本身。decode／load 不修檔。create 用先驗證、同目錄完整暫存、flush/fsync、排他建立，不能覆蓋大小寫同名或競爭檔案。save 不改呼叫端模型；成功回傳新 revision（parent 為讀取修訂）。只有 `status="saved"` 可清 dirty；`failed` 與 `conflict` 都保持 dirty。

Windows 更新使用拒絕原地寫入的讀取 handle，加上 [ReplaceFileW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-replacefilew) 的實際被替換檔備份。此 API 可能出現部分 rename 失敗，且不支援 WRITE_THROUGH flag，不能把一次函式呼叫當作全域 CAS。handle sharing 依 [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew)；非合作的另一個原生 ReplaceFile 仍可競爭，因此替換後另外檢查實際 displaced bytes。

| 結果 | 原件與候選 |
| --- | --- |
| 普通驗證／暫存／flush／替換前失敗 | 原文件路徑與 bytes 不變，候選仍由呼叫端持有 |
| 提前發現外改 | 不替換原路徑，回傳 conflict |
| 替換窗口才發現外改 | 同目錄 `.recovery-<id>.external` 保留實際外部版本，`.candidate` 保留候選，`.before` 保留已讀基準；回傳 conflict，不自動重試 |
| 替換後出錯／部分 rename | 保留實際已有的回復檔，回傳待處理 conflict，不假稱保存成功 |

回復檔是一般可讀的 JSON bytes，可用 `schema.decode(path.read_bytes())` 檢視；經使用者選擇後，可另存新名稱或重新讀原路徑再作明確保存。P1 不自動清理回復檔，`recovery_files` 可列出；後續 UI 要提供清楚入口。create 成功也保留獨立候選回復檔，附件是同目錄樹中的不可覆寫內容。

已驗的保存平台是 Windows 本機檔案系統。其他平台可以讀取 codec／附件，但更新既有文件會明確拒絕，直至有對應的可回復替換實作；不退回有競態的 hash-check＋replace。程序強制退出與故障注入不等於已驗實體斷電、硬體故障或遠端網路檔案系統的持久性。

## 重現與後續接點

從 checkout 根執行：

```text
python -m unittest discover -s tests -p "test_local_document_*.py" -v
python tools/local_workspace_p1.py --out <新的隔離輸出目錄>
```

工具會產生含完整分類、雙內部 Canvas 工作流、UTF-8 MD、附件與衝突回復檔的實物，刪測試索引、搬根後關閉原來源／DB 存取再讀回，並明確阻擋 Qt import。固定舊游標及 Stage fixture 沿原驗證邊界；沒有把 PNG 裁剪格式當文件 codec。

P2 接文件 registry/context、真 Qt 分頁與中央內容；P3 接檔案命令；P4/P6 才連接內容歷史、執行歸屬與跨介質交易。生成 metadata 仍走既有投影，P1 文件 envelope／索引沒有進入生成管線。前置版本的獨立驗收狀態不因本核心測試而改變。
