# 文件模式、檔案樹與編寫連結顯示

[開發指南](README.md) · [Markdown 內容](local-workspace/MD-C-CONTENT.md)

本文件記錄 Beta3 本機試用分支的顯示修正，不表示正式安裝版已更新。Chrome 儲存流程見[對話連接與保存](chrome-connector.md)。

## 文件模式跟隨目前選擇

右上模式選單的「編寫、原文、閱讀」是目前工作區的共用選擇。新開文件、切換既有分頁、分割窗格及開啟筆記連結都沿用目前模式；不再由每個分頁的舊狀態決定。選擇會保存在該工作資料目錄的 `workspace-ui.json`，重開同一工作區時沿用。Canvas 與管理頁沒有套用 Markdown 模式。

`WorkspaceShell.set_markdown_mode` 持有有效值 `write/source/read`，再交由 `MarkdownSurface.apply_mode` 套用視圖；後者不反向發布偏好。文件內容、dirty、buffer 與 Undo 歸屬保持既有文件層。

- `workspace_layout.RestoreView` 仍恢復游標與捲動，但在工作區中忽略舊分頁的 `markdown_mode`。新版分頁狀態不再寫這個欄位。
- 大文件 `source_ready` 讀取當下工作區模式，不使用載入前留下的舊模式；延後定位不主動搶焦點。
- 隱藏閱讀頁不為模式同步立即建立閱讀投影，顯示時才由既有生命週期刷新。
- 中文組字尚未完成時保留目前模式，避免切換中斷輸入。
- `run_workspace.open_initial_documents` 只在尚無模式選擇時採歡迎頁的初始閱讀模式；已有選擇時不覆蓋。
- `markdown_relations.open_source` 的普通筆記導航沿用工作區模式；明確要求編輯來源的動作保留原用途。

這是介面偏好增量，沒有改 Markdown 文件格式或搬移使用者文件。舊版讀取偏好時可忽略新增欄位。

## 檔案樹的文件名稱對齊所屬資料夾

`WorkspaceTreeDelegate.initStyleOption` 將既有文件負縮排規則一致套用至根層文件。根目錄的「歡迎使用」對齊 `files`；子資料夾內文件對齊各自所屬資料夾的名稱。展開與收合鄰近資料夾不改文件位置。

改動只涉及繪製，保留 tree model 的父子關係、實際路徑、點擊及文件操作。沒有新增模組、資料流或依賴。

## 編寫模式的連結標記成對收合

編寫模式原本只隱藏 `](網址)`，因此未編輯的連結留下開頭 `[`。`markdown_view.Syntax.highlightBlock` 現在利用既有連結解析取得完整範圍，成對收起前後語法，顯示完整連結名稱。游標進入該行時展開原有 Markdown；原文模式始終呈現完整語法。

高亮範圍仍由 Python 字元位置轉換至 UTF-16；不改寫 source、連結目標或 Undo。程式碼、跳脫標記、不完整連結及引用定義保留原有文字用途。原始內容的保存與重新開啟沿用原有編碼及換行處理。

## 有界驗證

- `test_markdown_global_mode.py`：真實模式選單、開檔／切頁／分割、未保存稿與復原、來源隔離、舊布局、重開、大文件晚載入、筆記連結、焦點及歡迎入口。
- `test_workspace_tree_alignment.py`：根層／多層對齊、展開收合、固定與浮動側欄點擊及合成截圖；既有檔案命令與點擊測例補做回歸。
- `test_markdown_link_presentation.py`：Qt 實際文字格式與點擊展開、中文及 emoji、括號／標題網址、literal 邊界、原文模式、保存重開與 Undo／Redo。

全部使用隔離合成文件與離屏 Qt；來源版本、命令及結果以本輪交付回執為準，不將背景測試當成實體 Windows IME／DPI 或使用者原資料接受。
