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

2026-10-15 補充四張主要功能畫面，取自公開來源提交 `da13609a84ebc867ba083139aff3cc4e8d5b1c4e`，執行標題為 `Prompt Calculus Studio · v0.8.8 Alpha 1`。

| 圖片 | 尺寸 | SHA256 |
|---|---|---|
| [canvas-overview-0.8.8.png](../../../images/canvas-overview-0.8.8.png) | 1920×1080 | `35d3d3af5487ceb4e7720fe3f135e5dcdd4d63e40fd0911886546a24a06bff86` |
| [canvas-functions-0.8.8.png](../../../images/canvas-functions-0.8.8.png) | 988×888 | `45bc51b97410d598ebe9956cfaae376b8bc3225ad5cf1814b11df79e995b5790` |
| [prompt-editor-preview-0.8.8.png](../../../images/prompt-editor-preview-0.8.8.png) | 1920×1031 | `cdc822fe2abe01fa40a21062fb200e9dc38f2bb28cf28e5c762c61619a5804d8` |
| [prompt-editor-flow-a-0.8.8.png](../../../images/prompt-editor-flow-a-0.8.8.png) | 748×681 | `6a4661267a4546dfdca81aa4eeefaf686b51f55f8883dc5653fd1d6dfbcb12be` |

使用實際 Qt 控件與 `QWidget.grab()` 離屏擷取，未重畫或修改圖片像素。Canvas 擷取整個視窗；功能模組頁擷取雙擊空白 Canvas 後開啟的選單面板；素材預覽擷取 Prompt Editor 頁面；流程 A 擷取點選預覽區「編輯」後的編輯面板。四圖已逐張檢視，文字與主要操作完整可見。

全新合成資料包含場景變體、文字、兩個組合及流程 A；流程 A 的三個組合分別重複 2／2／1 次。Canvas 只調整示例文件中的 CLIP 輸入與預排程位置，產品程式與預設布局未改。取圖使用 Microsoft JhengHei、實色材質，未讀取私人資料、連接 ComfyUI 或提交生成；網路／服務嘗試與 Qt 回呼例外均為零。圖片展示介面操作，沒有生成結果。

首次發布的兩張圖片與當時取圖紀錄保留於 [v0.8.8-alpha.1 驗證紀錄](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/blob/v0.8.8-alpha.1/docs/releases/0.8.8/validation/alpha.1.md#公開示例圖片)。

公開下載及包內容以[發布頁](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.8-alpha.1)及[來源對照](../source-provenance.md)為準；來源通過與最終成品／實際環境驗證分開記錄。
