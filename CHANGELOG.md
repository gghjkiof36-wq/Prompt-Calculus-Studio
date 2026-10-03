# 更新紀錄

各版本的下載與操作方式見對應 Release。

## 0.8.6 Alpha 1｜2026-10-03

- 整理 Canvas、媒體庫、探索、匯出與設定的共同導覽、配色、側欄、選單及提示；開啟程式直接進入 Canvas，原工作區內容和布局保留。
- 新工作區提供文字畫布、Prompt 輸出、預排程、CLIP、Stage、圖片預覽六個起始節點，以及逐步設定提示。
- 媒體庫增加縮圖與檔名檢視，清楚標示正在檢視的圖片；圖片資料可獨立開啟閱讀，選取多張後可一起匯出。
- 新增 Manager 套件清單、詳情、固定版本及支援環境的更新操作；配套 PCS 擴充可從程式內安裝，無須先連線或綁定工作流。
- 預排程可移除或取消單筆項目；底部取消只針對目前項目，全部取消改用明確選單操作。
- 改善背景頁面接收 Stage 操作、ComfyUI 前端 1.53.6 的參數相容、提示框快速切換及擴充側欄舊版標題。

提供來源主程式、同版擴充及校驗檔；來源內含配套擴充，安裝 Python 依賴後可雙擊 `Start.cmd`。下載、Canvas 實際畫面及問題處理見[發布說明](docs/RELEASE_NOTES_086.md)。

## 0.8.5 Alpha 1｜2026-10-02

- 新增 Stage 參數面板，搜尋並選取節點，修改 CFG、步數、尺寸及可編輯種子；檢視變更後套用，保存設定不會開始生成。
- 每筆等待任務可保存自己的參數，例如兩筆分別用 CFG 3／4；一般 Stage 後續改為 7，也不會改掉這兩筆設定。
- 節點分類可收合，種子與固定／遞增／遞減／隨機模式放在同一欄；改善長選單搜尋、捲動與浮點數顯示。
- 修正部分普通欄位誤判為唯讀、匯入後畫布未更新、放大模型下拉選單，以及 Ultimate SD Upscale 的原生種子控制。

下載原始碼主程式、同版 ComfyUI 擴充及校驗檔；安裝依賴後可雙擊來源包內 `Start.cmd`。更新擴充後重啟 ComfyUI 並重新整理網頁。參數唯讀與種子範圍的處理方式見[發布說明](docs/RELEASE_NOTES_085.md)及[Stage 參數指南](docs/STAGE_PARAMETERS.md)。

## 2026-10-01｜啟動與文件修正

- 雙擊 `Start.cmd` 使用安裝時建立的 `.venv` 啟動 0.8.4；環境尚未安裝時顯示安裝指引，啟動失敗時保留錯誤訊息。
- 修正 0.8.2 Release 的來源對照與安裝指南連結。
- 整理發布公告與操作指南，補上下載、啟動及常見問題的處理方式。

## 2026-10-01｜版本命名修正

此次更新修正了之前版本命名規則錯亂的問題。舊名稱與下載入口見[版本對照](docs/VERSIONING.md)。

## 0.8.4 Alpha 1｜2026-09-30

- 新增「執行階段（Stage）」，在畫布選擇生成工作流，安排執行順序與整串流程的重複次數。
- 將前一步生成的圖片交給下一個工作流，可選擇整批或指定圖片。
- 預先保存多組提示詞、圖片或生成步驟；等待中的項目可以排序、修改與移除。
- 修正種子更新、切換工作流及排程恢復問題。
- 按「執行」才將文字送入 ComfyUI；沒有接 Stage 時只更新輸入欄位。

提供原始碼版主程式、配套 ComfyUI 擴充與校驗檔。[下載與使用方式](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.4-alpha.1)。

## 0.8.3 Alpha 1｜2026-09-28

- 按執行即可送出生成工作，按設定次數使用當下的文字與圖片。
- 預排程保存接入的內容，在前一項完成後接續執行；圖片清單最多排入十個未完成項目，完成後自動補入。
- 新增圖片來源、圖片輸入與整批圖片供應。
- 底欄集中提供次數、執行、取消與任務數；取消只處理 PCS 提交的指定工作，ComfyUI 的 Run 可獨立使用。

[下載此版本](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.3-alpha.1)。

## 0.8.2 Alpha 1 Repair 5｜2026-09-27 公開 Alpha 預覽版

- 整合清單、Canvas、模型與圖片管理；CLIP 可選用 PCS 文字或 ComfyUI 手動文字。
- 生成前套用綁定的文字與圖片，保留其他未綁定的 ComfyUI 欄位。
- 整批圖片並排或網格預覽，點圖放大；修正較晚到達的舊縮圖覆蓋最新結果的問題。
- 預覽拔除圖片接線後清空，最近生成紀錄獨立保留；擴充版本不相容時提示更新。
- 改善清單操作、工作流選擇與重新連線。

本版每次執行一個工作流，使用時需保持 ComfyUI 網頁開啟。[下載此版本](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.2-alpha.1.repair.5)。

## 0.8.1 Alpha 2 UI Repair 1｜2026-09-14 04:07:49 UTC+8

[已公開預覽版](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.1-alpha.2.ui-repair.1)。提供來源 ZIP、ComfyUI ZIP、SHA256SUMS.txt，無 Windows EXE。此版含 Canvas、CivitAI 搜尋／下載及 UI Repair 1 修正；下載與啟動以該頁說明為準。

## 2026-09-15：公開首頁維護

提交 [048a3ac](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/commit/048a3ac0a49e698db5ec85b80b31a9964e67fd10) 更新 PCS 品牌、0.8.1 下載入口並區分當時尚未公開的 Repair4。

## 早期公開紀錄

0.8.0 及更早的開發版本名稱見[版本對照](docs/VERSIONING.md)。

## 早期對外文件整理

- 重寫首頁、原始碼安裝與 ComfyUI 操作說明。
- 補上 AGPL-3.0-only 授權、貢獻、資料與隱私及問題排查文件。
- 補上公開程式的範例介面截圖。
- 分開說明公開功能與本機 Alpha 開發方向。

## 2026-09-12：首次公開原始碼

對應提交 [`f170c46`](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/commit/f170c46e75acdf8e1dbd96e8d9680b7425ec103e)。

公開內容包含模組化 Prompt、輸出排序、手動稿、權重與排除 Tag、工作區、模型與圖片管理，以及 ComfyUI 側邊欄綁定、桌面控制、最近生成與模組快照。

這次公開提供原始碼，沒有發布安裝包。本機 Alpha 的後續功能不包含在此提交中，請見 [Roadmap](ROADMAP.md)。
