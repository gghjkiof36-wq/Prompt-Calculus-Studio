# Prompt Studio 0.81 Alpha 2 UI Repair 1

此版本提供 CivitAI 搜尋、下載與 ComfyUI 模型資產管理，以及畫布和設定頁修正。這是供測試的 Alpha 預覽版。

## 下載與啟動

- `PromptStudio-v0.81-alpha.2-ui-repair.1-source.zip`：本版完整專案原始碼及建置腳本。
- `PromptStudio-v0.81-ComfyUI.zip`：配套 ComfyUI 擴充；已使用 Maintenance 1 配套擴充者無需重装。
- `SHA256SUMS.txt`：下載檔案校驗值。

Windows 64 位元原始碼啟動（需 Python 3.12）：完整解壓原始碼 ZIP，在解壓資料夾開啟終端機，依序執行：

```powershell
py -3.12 setup_dependencies.py
py -3.12 run.py --v081-alpha
```

首次執行會建立本版的 data 資料夾。更新前請先備份舊資料；使用新的資料夾解壓，保留舊版。初次測試建議先使用空資料，不覆蓋日常版本。

擴充安裝：解壓 `PromptStudio-v0.81-ComfyUI.zip`，將其中的 `comfyui_prompt_studio` 資料夾放入 `ComfyUI/custom_nodes`，重啟 ComfyUI 並重新整理網頁。桌面端到「設定 → ComfyUI」設定連線與工作流。

Windows EXE 暫未提供公開下載：現有內部測試包的 Qt／PySide6 等第三方授權附件及對應來源說明仍需補齊。原始碼啟動會從 PyPI 取得套件，這次不重新散布其執行環境。

## 本版內容

- 設定頁內搜尋 CivitAI、查看縮圖與評價摘要、選擇版本和檔案，下載校驗後加入模型資產。
- 模型資產保存 Type、Base Model、來源、Trigger 與預覽；用途分類可管理。同名檔案不自動覆寫，登記失敗可重試。
- 圖片卡片、輸入後自動搜尋、可收合詳情、搜尋與圖片快取。預設使用 Red；空白搜尋顯示模型目錄。
- 設定自動保存，連線及資料入口集中；修復字典顯示、設定頁邊緣與材質、返回位置及 Canvas 按鈕排版。
- 集中變更通知、手動稿及欄位綁定入口、資料相容處理與版本打包設定，保留既有畫布及 Prompt 行為。

## 已有驗證與限制

開發紀錄：Maintenance 1 的 324 項回歸與 3 項建置檢查通過；UI Repair 1 的 87 項相關回歸通過。測試有重複，不加總。另已完成隔離資料保存／還原及離屏 EXE 檢查，公開搜尋、縮圖與小檔案下載已有測試。

仍待實機驗證：Windows 原生材質及多 DPI、Token／受限模型、大型下載、ComfyUI 載入新模型並完成 GPU 生圖。尚無斷點續傳或自動把模型接入工作流；未成年主題過濾依來源標記，不能保證辨識未標記內容。

本次發布檢查確認來源清單、下載包內容及校驗值，沒有重跑上述整套開發測試，也未執行 GPU 生圖。主程式碼與已交付 UI Repair 1 保持一致；只調整對外說明及文件中的私人路徑。

## 授權

專案採 AGPL-3.0-only，允許依條款商業使用，全文見 LICENSE。第三方元件依各自授權。商業替代授權仍在規劃中。
