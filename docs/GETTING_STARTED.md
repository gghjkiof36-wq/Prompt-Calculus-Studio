# 開始使用

[回首頁](../README.md) · 適用：0.8.6 Alpha 1

## 下載與安裝

從[0.8.6 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.8.6-alpha.1)取得 `PCS-v0.8.6-Alpha1-source.zip`，完整解壓到新資料夾。內含 `run.py`、`Start.cmd`、配套擴充及安裝腳本；請保留整個資料夾。本次提供來源版，沒有 Windows EXE 下載。

準備 Windows 64 位元版 Python 3.12（含 Python Launcher），在含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.6-alpha
```

首次安裝需要網路下載依賴。之後雙擊同資料夾的 `Start.cmd` 即可；它使用 `.venv`，啟動失敗時保留錯誤訊息。也可在 `Start.cmd` 按右鍵建立桌面捷徑。找不到 `py` 時，改用已安裝 Python 3.12 的完整路徑建立 `.venv`。

## 第一次使用 Canvas

啟動會進入 Canvas。新資料與新工作區提供六個已接線的起始節點；原工作區保持原本的節點、草稿與縮放。

1. 在「文字畫布」加入自己的模組與文字，從「Prompt 輸出」查看組合；可以先複製文字。
2. 打開「探索 → ComfyUI → 連線與工作流」，依下方步驟安裝配套擴充，再連接 ComfyUI 網址與工作流。
3. 在 CLIP 選擇實際文字節點／欄位；Stage 選擇生成工作流，依提示完成待設定項目。
4. 確認文字／圖片控制接入 Stage，再按底部「執行」。沒有有效 Stage 接入時只更新綁定輸入，不生成。

左上工作區膠囊可切換工作區、切回清單及開啟最近生成。頂列切換媒體庫、探索、匯出與設定，右上燈號表示 ComfyUI 連線狀態。詳見[操作指南](USER_GUIDE.md)。

## 安裝配套 ComfyUI 擴充

1. 在「連線與工作流」按「安裝／更新 PCS 擴充」，或從「管理與更新 → PCS 擴充」進入；不需要先連線或綁定工作流。
2. 按「選擇資料夾並安裝」，選 ComfyUI 根目錄、`custom_nodes` 或 Portable 的上層資料夾。
3. 先關閉 ComfyUI，再安裝。完成後啟動 ComfyUI，重新整理已開啟的工作流網頁。
4. 回 PCS 連線；在管理頁核對已安裝與實際載入版本。更新檔案不會立即改變已開啟的服務。

擴充提供 PCS 連線與網頁功能，不新增生成節點，不會出現在新增節點選單。來源包已含同版擴充；另提供的 `PCS-v0.8.6-Alpha1-ComfyUI.zip` 可用於手動安裝。操作與恢復方式見[ComfyUI 整合](../COMFYUI_GUIDE.md)。

## 更新與資料帶入

更新前備份，關閉舊 PCS，將新版本放到新資料夾並使用資料副本。預設資料位於 `run.py` 旁的 `data`，可用 `PROMPT_STUDIO_DATA` 另指定位置；新舊程式不要同時連接同一服務或寫同一資料庫。

配套擴充的安裝位置和自動更新選項保存在目前資料目錄。沿用副本時先核對目標位置；使用全新資料時需重新指定。開啟「隨 PCS 自動更新配套擴充」後，只在 ComfyUI 關閉時安裝本版隨附包，不會自行從網路下載最新版。主程式與擴充請使用同版，更新後重啟 ComfyUI 並重新整理網頁。

未完成流程重開後先暫停，確認內容再繼續；保存與 PNG 恢復不會自動送出任務。沒有參數設定的舊 Stage／等待項不會自動補入當前面板值；參數操作見[Stage 指南](STAGE_PARAMETERS.md)。0.8.3 的等待項需重新安排。

回退時停用擴充自動更新，使用相容的舊程式、舊擴充與原資料副本，保留新版新增內容；不保證自動合併。外部模型、圖片、擴充資料與憑證分別備份，見[資料與隱私](DATA_AND_PRIVACY.md)。SHA256SUMS 用於檢查下載完整性，不是數位簽章。舊版入口見[版本對照](VERSIONING.md)。
