# 0.8.0 Alpha 收尾與 CivitAI 草稿審查

日期：2026-09-13。這次交付維持 v0.8.0 Alpha，不發布正式 0.9.0。

## 獨立版本與資料

- 0.8.0 測試程式：`build/v08-alpha-3-hotfix-2/PromptStudio/Start-v0.8-Alpha.vbs`。
- 相配擴充：同資料夾的 `PromptStudio-v0.8-ComfyUI.zip`。
- 修改前備份：`build/v08-backups/alpha3-hotfix2-20260913-205342/`，包含原程式、SQLite 一致性副本、圖片資料及當時未提交的完整原始碼。
- CivitAI 獨立工作目錄：`build/civitai-review-20260913-210744/workspace/`。
- 草稿修改前封存：該審查目錄中的 `source-before-isolation.zip`、`working-tree.diff`、`source-manifest.json`。

修正包 2 的 `data` 由修正包 1 複製，未覆寫正在使用的版本。畫布模式及清單模式逐份核對編譯文字與手動稿，遷移前後一致。為處理此次固定 Seed 重複生成，僅把副本中目前活動工作流改為每次隨機；原值、工作流 ID 及文字摘要雜湊記錄於 `v08-hotfix2-migration.json`。

## 0.8.0 實際修改

| 檔案 | 變更原因與行為 |
| --- | --- |
| `run_controls.py` | 只套用最終顯示狀態，不再於更新中短暫顯示活動任務文字，避免按鈕寬度跳動。 |
| `multi_canvas.py` | 移除圖片卡片接線捷徑；新來源卡片放在目標 Prompt 附近，換圖納入復原紀錄。 |
| `canvas_results.py`、`window.py` | 依結果所屬輸出選擇導入位置；修正包在視窗標題中顯示明確版本。 |
| `generation_panel.py`、`settings_page.py` | 導入圖片保留活動工作流；移除 Alpha 的文生圖／圖生圖操作切換入口，以欄位綁定控制輸入。 |
| `generation.py`、`generation_runner.py`、`composition_image.py` | 沒有圖片連線時只寫入文字、保留工作流原圖；有圖片但沒有可接收欄位則提交前報錯。圖片綁定與採樣器選擇分開驗證；已連接但不可讀的來源仍會阻止提交。 |
| `workflow_import.py`、`workflow_transfer.py`、`workflow_manager.py` | 從保存的 ComfyUI 畫布保留 Seed 規則；API 檔缺少規則時預設隨機。支持固定、遞增、遞減、隨機。 |
| `build_windows.py`、`tests/test_v08_hotfix2.py` | 獨立封裝入口及本次缺陷回歸。 |

只有執行中的有效文字綁定參與提交。沒有黃線從畫布輸出至參與執行的 Prompt，該畫布圖片就不會送出；有圖片卻沒有工作流圖片欄位時，不會悄悄省略。原有唯一欄位綁定、輸出排序、手動稿及提交快照繼續保留。

## 問題來源與驗證

第一輪完整 0.8.0 回歸共 250 項，249 項通過；唯一失敗是 CivitAI 新增第四個設定分頁，違反原有三分頁介面約定。圖片位置、執行按鈕及生成輸入問題屬於原有 0.8.0 問題；沒有證據將它們歸因於 CivitAI。

隔離 CivitAI 後，250 項全部通過；之後補上圖片綁定與採樣器分支獨立的情境，並重新執行最終完整回歸。最終數量與成功狀態以「驗收紀錄」中的 `delivery.json` 和原始測試日誌為準。

Windows EXE 使用獨立空資料夾做無視窗檢查，覆蓋保存／快照還原、小視窗與大字級、閒置重繪及圖片快取。畫面比較亦由程式本身在無視窗環境繪製，沒有擷取或操作使用者桌面。

本輪依使用者要求不啟動實機生圖、不使用 GPU、不切換視窗。實際 ComfyUI 雙文字生成、底圖上傳生成品質及原生 Windows 視窗／不同 DPI 操作保留待驗，不能以隔離或無視窗測試代替。

## CivitAI 的隔離方式

先封存完整工作目錄，再將 `pages.py`、`media.py`、`settings_page.py` 與修正包 1 原始碼逐項比較，只移除辨認出的 CivitAI 整合改動。`settings_page.py` 本輪移除模式切換的修正保留。

三個 CivitAI 新模組與兩個原始測試檔均先核對副本 SHA256，再從 0.8.0 移出。其餘檔案逐份核對未因隔離被改動；沒有整包還原、重設 Git 或刪除資料庫既有內容。

草稿的後續修正僅存在獨立審查目錄。六項問題、對應測試及限制見 `CIVITAI_DRAFT_REVIEW.md`；是否把草稿接回介面仍須另行確認。本輪未新增 Trigger 自動套用、收藏、評分、History 或 Recipe。
