# 0.8.8 Alpha 1 驗證範圍

## 固定來源

受驗功能來源：`0d4a5f5a9ef51a98137a18522c0d2bf110ca3b61`，完整差異起自公開 0.8.7 的 `705dd90a9d606fd68876d8ffe97ce10b9984b015`。版本身份、文件整合及成品另依[來源對照](../source-provenance.md)確認，不能用原候選 5 包代替修正版。

本頁整理來源階段已有檢查，不將開發自驗、獨立複驗、包清單及實際服務結果相加成全版通過數。

## 已核對的行為

| 範圍 | 結果與方法 | 可追溯入口 |
|---|---|---|
| 三層素材與取值 | 固定／遞增／遞減／兩種隨機、重複跨批、十對六、畫布當批固定、同位置分線、保存失敗／取消／重試等逐筆資料檢查 | [取值契約](../../../../tests/test_prompt_material_resolution.py)、[素材介面](../../../../tests/test_prompt_material_ui.py)及[候選 1 當批紀錄](../development/candidate1.md) |
| 保存與舊進度 | 真舊格式資料、巢狀位置與失效簽章反例分開核對；舊 5／6 升 7 的有效重複進度修正通過原組複驗 | [遷移測試](../../../../tests/test_prompt_material_migration.py)、[資料契約](../../../development/data-compatibility.md) |
| 取消全部 | 真 Qt／SQLite 到合成執行器，核對日誌、所屬取消、未知／晚到回覆、其他工作區及外部任務保留；原提報組複驗通過 | [綁定後排程](../../../../tests/test_bound_scheduler.py)及[操作](../../../guide/scheduling.md) |
| 生成資料隱私 | 實際 envelope 及合成 PNG 不帶未用素材正文／分類／資料夾，必要引用及完整備份仍可還原；限定差異獨立複驗通過 | [快照隱私](../../../../tests/test_snapshot_privacy.py)、[Stage 投影](../../../../tests/test_stage_snapshot_privacy.py)、[分類](../../../../tests/test_snapshot_categories.py) |
| 多輸出順序 | 切空／非空輸出、保存重開、編輯／復原及手動排序的真 Qt 反例修復；保存／還原及隱私差異另核 | [目前資料契約](../../../development/data-compatibility.md#生成投影收納及完整多輸出順序) |
| 編輯、預覽與布局 | 分類拖移、變體、右側子頁、素材預覽、獨立圖片預覽與窄窗檢查；Stage 鍵盤預覽原例修復後通過 | [素材編輯](../../../../tests/test_material_editor.py)、[預覽](../../../../tests/test_material_preview_variants.py) |

以上包含來源自驗與原提報組的限定複驗，使用合成資料及替代傳輸，沒有把合成原生任務視為 GPU 生成。舊候選、原始失敗、超時及後續修正紀錄保留，未用後來通過覆蓋原始失敗。

## 保留的問題與未完成驗證

- 特定舊候選 5 Stage PNG 原版也拒絕還原；新生成投影及六份合法真舊完整快照通過，不代表所有舊 PNG 已修。存量圖片修復延後，避免接受失配快照，保留原檔與資料備份。
- 六類低等級測試維護尚未處理：分類標題、工作區快取字典、舊格式 fixture／資源清理、像素／sizeHint、舊文件版本斷言、已撤回的舊編輯測試入口。實際資料、Undo／Redo、重開及現行入口另有補驗；原 fail／error 不計通過，不為舊斷言改回已確認功能。
- Reader 強制 DeferredDelete 壓力情境與正常關窗分開；正常可達流程未重現的仍待查，不稱已修。
- 原生桌面完整鏈未完成：一次合成主畫面受開始選單覆蓋，沒有發出操作，程序後來已退出並留原生異常，原因未定。離屏顯示不證明正常原生冷啟／退出、多螢幕、全部 DPI 或瞬態閃爍已驗收。
- 部分前端來源檢查因缺指定資產而未驗。真實服務／GPU、新機、正式安裝替換及所有第三方節點未驗；歷史準備版真 0.40 紀錄僅適用當時來源，見[相容指南](../../../guide/comfyui-compatibility.md)。

## 公開示例圖片

Canvas 與 Prompt Editor 圖使用固定功能來源的真實 Qt 控件、全新合成資料、實色材質與明確中文字型，離屏擷取。沒有連接 ComfyUI、提交生成或讀取私人資料；圖只展示介面，不能證明生成品質或原生材質效果。精確來源、尺寸及 SHA256 隨圖核對後記於下表。

| 圖片 | 尺寸 | SHA256 |
|---|---|---|
| [canvas-0.8.8.png](../../../images/canvas-0.8.8.png) | 1920×1080 | `77aed2c6eafc03e9eda89eb7c1699f9efddf3c075b522fb269c7f43b82058774` |
| [editor-0.8.8.png](../../../images/editor-0.8.8.png) | 1920×1031 | `0b528e9793a83b6536771aa744328ae903d75dcbc9e41eead8119d6f4c64c350` |

Canvas 使用視窗擷取，Editor 使用實際頁面控件擷取。功能源與上方固定提交相同；該功能樹的 runtime 標題仍沿用 0.8.7 Alpha 1，正式發布身份由後續版本整合設定，沒有為圖片另改產品版本。擷取時使用 Microsoft JhengHei、實色材質，配套啟動探查停用，網路／服務嘗試及 Qt 回呼例外均為零；僅適用這次取圖。

公開下載及包內容以[發布頁](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.8-alpha.1)及[來源對照](../source-provenance.md)為準；來源通過與最終成品／實際環境驗證分開記錄。
