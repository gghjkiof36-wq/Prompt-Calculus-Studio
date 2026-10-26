# UI 元件與互動原則

[開發指南](README.md) · 基準：0.8.9 Beta2，來源 `5aa00c146281d96dd9d12f45ae020cfbe6e9575e`。

新功能先沿用同版的共用元件與已確認操作，再補用途需要的變體。統一的不只有配色，也包括外框層級、選單行為、焦點、取消與資料保護。本頁整理現有實作及後續驗證要求，沒有宣稱所有既有畫面都已完成一致性驗收。

## 工作區畫面動畫

Beta3 的 `workspace_ambient.WorkspaceAmbient` 是工作區內的透明裝飾層，預設關閉，不取得焦點、不接收滑鼠事件，也不寫入文件或觸發生成。沿用現有介面個人化表單與 `workspace-ui.json`；還原設定可回到原先選擇。粒子數隨面積調整，上限 36，單一 33 ms 計時器只更新變動區域。背景、隱藏、最小化、應用及 Windows 減少動畫設定會暫停效果。驗證入口為 `test_workspace_ambient.py`。

文件頁排在 surface edge 下方；`WorkspaceSurface.sync_surface` 調整側欄／分割布局並抬高 edge 後，須同步抬高 ambient 與通知，避免下一次 `sync_page` 把文件排到粒子上方。保留原 edge 對文件及 home stack 的順序，不能單把 edge 降到 ambient 下方：那會讓背景 stack 擋住文件輸入。布局同步不重建粒子、不新增計時器。測試涵蓋雙 Markdown 頁、Canvas、側欄收合、分隔調整、頁面切換、可見區域及輸入穿透。

33 ms 僅控制裝飾粒子，不限制 Canvas 操作繪製。Canvas 仍按事件更新；效能驗證應分開記錄操作處理、繪製、提交及實際螢幕呈現。120 Hz 的單幀預算約 8.3 ms，240 Hz 約 4.2 ms；固定 16 ms 的測試輸入或 offscreen paint 次數不能證明高更新率達標。

Workspace 文件各自擁有內容，設定不再顯示舊共用視圖的 `separate_selections` 開關。儲存外觀設定時須保留該歷史欄位及舊草稿，不把隱藏選項轉成資料遷移。管理頁來源清單同時檢查文件種類、副檔名與檔案存在性；保留缺檔頁的恢復模型，不等於把它當可匯入素材。

## 依用途選元件

### Canvas 素材與文件入口

Canvas「加入素材」是所屬文件內的非模態浮層，使用 `CanvasPalette.present()`
與 `finished` 收回暫時的加入目標。不得使用 `exec()` 鎖住工作區；側欄、分頁與
懸停側欄維持可用。點導航時先關閉浮層，再由原點擊完成導航；點遮罩空白僅關閉，
不把點擊傳入 Canvas。Escape 優先關閉浮層，不觸發底下頁面的返回捷徑。
來源頁隱藏時也收掉浮層，避免返回時帶回過期目標。選取與匯入仍經原文件交易，
保留來源副本、固定目的地、Undo／Redo 與保存流程。

初始內容根（預設 `files`）是側欄的隱含入口，其子項直接顯示於第一層；磁碟位置
及已加入的其他資料夾連結不變。空白處取消選取後新增，目的地為初始內容根；
選取資料夾時在其內新增。文件／子資料夾拖至側欄空白處或選「移動到 → 最外層」可回到
初始根，仍沿用原本同根、不覆寫的搬移與引用處理。

新增先排他建立「未命名」，碰撞使用既有 `(1)`、`(2)` 編號，再提供可選的行內
改名。Escape、留空或離開輸入框保留已建立項目；手動名稱無效或重名時仍在原
輸入框顯示錯誤，不覆寫另一文件。首頁與側欄使用相同的預設名稱規則。
驗證入口：`test_workspace_canvas_creation.py`。

| 用途 | 選用 | 原因與接入注意 |
| --- | --- | --- |
| 桌面主視窗外框 | [window_chrome.install_expanded_chrome](../../prompt_calculus_studio/window_chrome.py)，參照 [WorkspaceShell](../../prompt_calculus_studio/workspace_shell.py) | 保留 Windows 原生 frame、resize／最大化等能力。顯示前安裝，預留 caption inset 並跟隨螢幕變更；支援路徑有平台／Qt 版本檢查，不支援時回傳 `None`。不要為統一圓角把主視窗改成 Frameless。 |
| 視窗內部殼面 | [WindowShell](../../prompt_calculus_studio/widgets.py) | 承接底色／材質、邊緣 resize 與游標清理；它與原生外框分工，不是整套視窗控制的替代品。 |
| 一般應用內表單 | [StudioDialog](../../prompt_calculus_studio/widgets.py)＋[module_chrome.dialog_context](../../prompt_calculus_studio/module_chrome.py) | 共用標題、關閉、主題和字級。小型表單用 `body`，多欄編輯用 `editor_area()`／`DialogEditor`，footer 動作留在外殼。 |
| Canvas 浮出編輯／只讀小面板 | [CanvasSheetDialog](../../prompt_calculus_studio/dialog_presentation.py)／[DismissibleSheet](../../prompt_calculus_studio/widgets.py) | 沿用對應遮罩、借用內容還原與關閉生命週期。只讀 popup 與可修改表單的接受／取消義務不同，依用途選用。 |
| 操作與右鍵選單 | [RoundMenu](../../prompt_calculus_studio/widgets.py) | 先連接 action，再用非阻塞 `open_for()` 或 `open_at()`。按鈕入口優先 `open_for()`，共用 Canvas 座標轉換與螢幕邊界定位；子選單由 `addMenu()` 建立並持有至根選單關閉。 |
| 固定選項／字體選擇 | 共用 [ComboBox／FontComboBox](../../prompt_calculus_studio/widgets.py) | 保留 Qt 選取語意、文字量測、長名稱提示與長清單捲動；Escape 的暫選不能成為下次 Enter 的意外提交。 |
| 選單與下拉的表面 | [PopupSurface](../../prompt_calculus_studio/popup_surface.py) | 共用圓角、邊線、列高與寬度量測。背景只取所屬 PCS 視窗，避免擷取桌面內容；功能頁不另畫一圈外框。 |

`RoundMenu` 的非阻塞要求針對這個自訂原生選單路徑：原碼註明支援 Qt 組合下 `QMenu.exec()` 曾發生崩潰，因此此元件採非阻塞路徑。**不要擴大成所有 Qt 選單都改非阻塞**；Qt 自建的文字編輯選單保留自己的 action 回傳與剪貼簿行為，`NativePopupFinish` 只整理外觀／事件。`StudioDialog.exec()` 也有其獨立生命週期。

## 外觀共用來源

色彩與明暗層級以[應用程式配色基準](application-color-baseline.md)為準：`theme.visual_tokens` 是樣式表、原生 palette 及自繪元件的共同來源，`color_roles` 派生互動與語意色。新增畫面引用角色，不再維護另一套灰階／色碼表。主要動作、焦點、選取與錯誤各有含義，不能只靠換顏色表示狀態。

字體和字級沿目前工作環境設定及 `font_pixels`；彈窗、巢狀彈窗、選單應保留同一條外觀來源。間距、圓角與列高目前由共用元件／樣式提供，尚無完整獨立的 spacing-token 系統：先使用 `StudioDialog`、`DialogEditor`、`popup_row_height` 等既有布局，真正需要共享新尺寸時再抽到相應共用來源。不要把某頁的固定像素複製成全產品規則。

比較新舊頁面時，以同一版本、主題、字級及用途的基準頁對照，核對內容邊界、分組、主次、留白與操作位置。選單、表單和文件編輯器不必有相同密度；差異應能用任務需要解釋。避免以增加常駐說明補救布局或操作對象不清。

## 互動與狀態一起檢查

| 情境 | 應保留的結果 |
| --- | --- |
| 正常、hover、focus、pressed、selected、disabled | 文字與圖示可讀，焦點可辨識；停用項不觸發。使用既有角色和控制項語意，不把 selected 當 hover。 |
| 空白、載入、成功、部分完成、失敗、取消 | 清楚指出目前對象與真實狀態；長錯誤／恢復資訊可到達末尾，必要時可複製。完成寫入的回執不能因關頁改成「取消」。 |
| 打開表單、巢狀表單及返回 | 明確指定初始焦點、Tab 順序、預設動作和關閉後可繼續的位置。`StudioDialog` 不自動替每份表單保證這些結果。 |
| Escape、×、取消、點外側 | `StudioDialog` 的 × 走 `reject`，外側關閉預設未啟用；啟用前確認是否允許捨棄暫選。業務層決定取消不寫入、接受只提交一次及未保存稿保護，不能只依賴外觀殼。 |
| 選單外側點擊、重新開啟、子選單往返 | 共用 no-replay 行為避免同一次關閉點擊又觸發下方入口；下一次點擊與鍵盤仍正常。子選單隱藏不應提早刪除其 action。 |
| 窄視窗、大字級、長名稱、畫面邊緣 | 主要操作可到達，長內容可捲動或有完整標籤提示。保留共用量測與翻轉定位，不以裁切隱藏問題。 |
| 主視窗還原／最大化、切到其他程式後返回、多螢幕 DPI | 操作區、系統按鈕、安全區、resize、焦點與關閉保護正常。原碼有 DPI／screen-change 處理，但實機結果仍需對受影響流程確認。 |

關閉視窗涉及保存與恢復，須遵守[架構參考](architecture-reference.md)和[關閉恢復](workspace-close-recovery.md)。不得讓動畫、遮罩或新的視窗殼繞過原先的拒關條件。

## 合理變體與真正例外

下列是既有標準變體：小型表單使用較亮的欄位，多欄編輯器使用 `DialogEditor` 分層；執行／停止、警告、連線與節點接線保留語意色；使用者圖片、文件內容及原生檔案選擇器不重繪成應用色盤。原有材質、透明度和文件布局設定也有其用途。

[Canvas 基準](canvas-visual-baseline.md)保留專屬操作與歷史背景；其中舊「非 Canvas 保留原色」及逐頁遷移狀態不是現行例外。現行應用內彈窗沿共同配色，不因從另一頁開啟而切換整套樣式。

如果新用途確實需要偏離共用行為，在功能說明中記三件事即可：使用者需要什麼、哪個行為／元件因此不同、如何驗證既有用途沒有被破壞。一般變體由實作判斷；改變範圍外已確認功能、資料或 UI 契約時，再交代具體取捨。無須新增全域 UI 簽核機制。

## 驗證入口與交付方式

| 改動 | 可沿用的檢查入口 | 仍需實際走過的流程 |
| --- | --- | --- |
| 外框／系統按鈕 | [test_window_chrome.py](../../tests/test_window_chrome.py)、[test_window_menu.py](../../tests/test_window_menu.py) | 還原、最大化、縮放、切回、原生系統選單與拒關；依影響檢查實機 DPI。 |
| 選單／下拉 | [test_popup_visual_contract.py](../../tests/test_popup_visual_contract.py)、[test_popup_surface_final.py](../../tests/test_popup_surface_final.py)、[test_popup_menu_ownership.py](../../tests/test_popup_menu_ownership.py) | 點擊、鍵盤暫選與取消、重新開啟、子選單、畫面邊緣和長文字。 |
| 主題／表單／浮層 | [test_color_roles.py](../../tests/test_color_roles.py)、[test_visual_theme.py](../../tests/test_visual_theme.py)、[test_canvas_popup_layers.py](../../tests/test_canvas_popup_layers.py) | 三種既有主題、大字級、巢狀彈窗、焦點、取消／保存、受影響頁面的完整操作。 |

這些檔案是驗證入口，不是本次新跑結果。離屏可檢查布局、狀態與 Qt 契約；例如 no-replay 屬性的檢查不等於 Windows 真實點擊重播已驗收。原生材質、跨螢幕 DPI、輸入法、焦點與使用者視覺接受需各自證據，不能由截圖張數或靜態清單代替。

新 UI 交付記錄固定版本、基準頁／共用來源、完整操作及關鍵狀態，截圖註明版本和環境；有行為例外則連回理由。元件本身或原則改變時同次更新本頁，色彩數值繼續只在既有基準與共用來源維護。分工見[開發指南](README.md#隨功能維護)。

### 重點色選單與自訂色彩

- `accent_colors.py` 定義 Qt-free 的持久化選項與六位 HEX 驗證；新增色名保持既有 neutral／blue／green 的語意。`accent_custom` 獨立保留上次自訂色，切換預設色不抹除它。
- `accent_picker.py` 沿用共用 ComboBox／PopupSurface。選單以色點、名稱及向量勾選標記表示目前設定；自訂同列提供選色按鈕與 HEX 欄位。小型 HSV 浮層支援滑鼠、方向鍵與 Shift 加速；套用才提交，Escape／取消／點外側保留原值。
- 不完整或非法 HEX 不覆寫上次合法色碼。Workspace 顯示偏好儲存在使用者介面設定，不寫回開啟的文件內容。
- 填色保留指定 HEX；`accent_outline` 在現有表面上維持 3:1，按鈕文字由共用 color_roles 調整。選色控制的色點保持原色，不隨選取狀態染色。
- 有界驗證：`test_accent_picker.py`、`test_color_roles.py`、`test_visual_theme.py`、`test_maintenance_package.py`，另以背景 Qt 截圖檢查選單與自訂浮層。這些不代表原生 Windows 高 DPI／IME 全面驗收。

### 組合直接輸入的圖片 Prompt 入口

- `GroupEditor` 的「直接輸入」共用頁提供「從圖片匯入…」，內容欄與按鈕都接收單張本機 PNG 拖放。`MetadataPromptImport` 一次捕獲最多 25 MiB 原始 bytes，經 `png_metadata_stream` 與既有 `metadata_sources` 讀取，保留 CRC／壓縮 metadata 上限；不解碼像素、不複製原圖、不連網。
- 只採用 `role=positive`：ComfyUI 依 positive 接線追蹤，parameters 依正負分隔解析。負面與未判定文字不會自動當成正面；多個不同正面來源須選擇。沒有可用來源、檔案損毀／不支援／過大時，維持原內容並提供就地訊息。
- 空稿且單一來源直接填入。已有文字時以共用 StudioDialog 預覽並選附加／取代／取消，附加是預設；接受時再次核對原組合與內容。使用 QTextCursor edit block 保留編輯框 Undo／Redo，再沿既有素材草稿、DocumentOperations、保存與恢復路徑；匯入本身不發布素材。
- `test_metadata_prompt_import.py` 驗證真實 Qt 拖放、原圖 bytes、正負與多來源、取消、草稿失效、匯入 Undo、素材保存／重開／Undo／Redo。`test_prompt_group_ui.py` 沿用其他組合流程；既有 uncategorized 斷言問題另留基準對照，不列全綠。
