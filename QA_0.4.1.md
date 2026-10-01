# Prompt Studio 0.4.1 驗證

2026-09-11，Windows 11 build 26200，PySide6 6.11.2，Windows 縮放 125%。

## 結果

- 27 項功能測試通過。新增顯示重新整理時，手動稿、游標、偏好、收合欄位及停用狀態保持不變的檢查；保留先前清除／取消、複製回饋、排序和選單測試。
- 最終封裝 EXE 的 12 項原生外框檢查通過：solid / mica / acrylic 各自正常、最大化、最小化、還原。每次都保有 WS_CAPTION、WS_THICKFRAME、系統最小化／最大化按鈕，沒有 WS_EX_LAYERED，Qt mask 為空。DPR 1.25、Windows DPI 120、提示詞邏輯字級 16 px 維持一致。
- 最終封裝 EXE 的 23 項選單操作檢查通過，包含工作區設定、新增、刪除取消／確認、資料、素材、模型、圖片及組合入口。測試資料獨立，沒有操作使用者的刪除項目。
- 使用原資料庫的獨立副本啟動新版成功，載入 8 個提示詞；原資料庫 integrity_check 為 ok，測試前後 SHA-256 一致。
- tests/ui_review.py 通過：正常／手動／複製狀態、三種視窗尺寸及放大字級。選單極角為透明、儲存／取消按鈕等高。元件截圖在 qa/ui-v041。

## 實際桌面核對

以 Windows Computer Use 開啟 build/package/PromptStudio/PromptStudio.exe 的獨立資料副本，直接查看桌面合成畫面，而非只依 Qt 元件截圖判斷。

- Mica 標題列與周邊呈現暗色背景色調。
- 切換 Acrylic 後，上方與四周可看見背景的模糊色塊，提示詞與素材內容區仍為深色。
- 畫面中未見原先自繪大圓角外的灰色矩形角落；現在採 Windows 標準圓角，沒有額外主視窗遮罩或第二圈自繪外框。
- 使用原生最大化按鈕後，右上圖示變成重疊方框；還原回一般大小正常。
- 實際右鍵選單開關正常，四角未見先前灰白殘片。
- 操作測試副本時偵測到使用者接手，保留當前視窗，不中斷其設定操作。正式版本仍從根目錄 Start.cmd 啟動並使用 release/PromptStudio/data。

## 證據與界限

- 外框結果：qa/final-frame-041-9cbaa1ef008c4ae5b3b023b137bb225f/frame-result.json。
- 選單結果：qa/final-menu-041-6a37c0c42d794be387552d0734b3cdbe/menu-result.json。
- 原資料副本啟動：qa/existing-data-041-n73r27vg/smoke-result.json。
- 初次以 Start-Process -WindowStyle Hidden 啟動視窗診斷，作業系統的隱藏啟動狀態使最大化檢查失敗。改用正常 GUI 啟動後 12 項全部通過；沒有刪除失敗紀錄或放寬斷言。
- 此次已直接目視桌面材質，但不宣稱在所有 Windows 版本、背景或透明設定下效果完全相同。
- 睡眠後介面縮小依使用者要求不優先處理。只驗證合成的顯示／恢復通知與資料保留，未讓電腦實際睡眠，不列為已修復。

## 外框修正依據

0.4.0 的透明無框視窗與 QRegion 會阻止 Windows 原生圓角。本版保留實際原生框架，由 DWM 管理外輪廓和標題列，移除自繪 TitleBar、Shell 圓角及主視窗 region。參考 [Microsoft 圓角限制](https://learn.microsoft.com/en-us/windows/apps/desktop/modernize/ui/apply-rounded-corners) 與 [Qt 6.11.2 原生框架／分層判斷](https://github.com/qt/qtbase/blob/v6.11.2/src/plugins/platforms/windows/qwindowswindow.cpp)。
