# 應用程式配色基準

0.8.9 開發版以既有 Canvas 卡片作為色彩來源。首頁、Prompt 管理頁、媒體庫、探索、匯出及設定使用相同面板、欄位與互動角色；應用程式內彈窗也不再依開啟頁面切換成另一套外觀。

畫面以中性背景承接內容，稍亮的面板區分操作群組，深色欄位表示可輸入區。重點色留給主要動作、焦點和已選物件；標題與一般圖示使用文字色。較亮的浮動選單仍與固定面板有層級差異。

## 色彩角色

| 用途 | 共用來源 | 石墨主題 |
| --- | --- | --- |
| 文件／Canvas 底色 | `canvas_background` | `#111111` |
| 固定面板與卡片 | `surface` / `module_body` | `#232628` |
| 輸入區 | `field` / `module_field` | `#191c1e` |
| 模組標頭、浮動選單 | `module_header` / `raised` | `#282c2e` |
| 一般邊線 | `line` / `module_line` | `#404243` |
| 滑過／選取 | `hover`、`selected` | `#2f3234` / `#3c3e3f` |
| 彈窗外殼／邊線 | `module_dialog`、`dialog_line` | `#191c1e` / `#444647` |
| 主文字／次要文字 | `text`、`secondary` | `#f1f0ea` / `#bebeba` |

霧藍與暖白主題沿用各自已存在的 Canvas 卡片色，不重新設定使用者的配色或重點色。小型單層彈窗的輸入框比外殼稍亮；多欄編輯器使用稍亮的 `DialogEditor` 面板及較深的欄位，延續 Canvas 原先的分層。

`theme.visual_tokens` 是 Qt 樣式表、原生控制項 palette 及自繪元件的共同入口。`color_roles` 派生互動、文字及語意色；新增元件使用角色，不自行加入另一組灰階。文字、次要文字及提示文字對背景至少保持 4.5:1，重點色的文字在一般、滑過和按下狀態都須可讀。勾選框等小型控制標記保留較強的 `control` 色，不以裝飾邊線代替。

## 彈窗與特殊顏色

`StudioDialog` 經 `module_chrome.dialog_context` 取得目前工作環境的主題；巢狀彈窗沿用父層。`window_appearance` 可從子視窗回查所屬工作環境，避免編輯器由另一個彈窗開啟時遺失主題或字級。標題、關閉圖示、主要按鈕與原生 palette 同步套用。

執行／停止、警告／錯誤、連線狀態及節點連線各有操作含義，保留語意色。圖片、使用者內容與作業系統檔案選擇器不以此色盤重繪。導覽位置、視窗材質、透明度與文件布局仍依原有設定。

## 驗證入口

`test_color_roles.py` 核對三種主題、各重點色、文字對比及既有 Canvas 色；`test_visual_theme.py` 檢查實際 Qt 欄位及圖示；`test_canvas_cohesion.py` 檢查不同頁面、切換主題及巢狀彈窗。選單與操作按鈕另由 `test_popup_visual_contract.py`、`test_popup_surface_final.py`、`test_action_visuals.py` 檢查。

背景 Qt 畫面涵蓋主要頁面、共用重新命名／圖片資料／檔案搜尋彈窗及較窄視窗。離屏結果不代表 Windows 原生材質、實機 DPI 或使用者視覺接受；各次交付另外記錄固定來源、實際結果和已知限制。
