# 開始使用 0.8.9

[更新內容](../releases/0.8.9/repair.1.md) · [驗證與限制](../releases/0.8.9/validation/repair.1.md)

## 來源版

將 `PCS-v0.8.9-Alpha1-Repair1-source.zip` 完整解壓到新的資料夾。使用 Windows 64 位元 Python 3.12，在含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-alpha
```

首次安裝依賴需網路，之後雙擊 `Start.cmd`。首次會開啟「歡迎使用.md」，從裡面的連結進入「教學素材」。之後會接續已保存的分頁；關閉歡迎頁後，不會每次強制插回。教材可直接編輯，想保留初始內容可先複製文件。

## Windows 本機成品

完整解壓 Windows ZIP，保留 `_internal`、`extensions` 及其他檔案，再雙擊 `Start-PCS-v0.8.9-Alpha1.cmd`。這個成品已含執行環境，不需要另外安裝 Python。它目前僅供本機使用，公開散布狀態以發布端確認為準。

## 內容與資料

預設 `files` 保存新文件，`data` 保存設定、工作區記錄及內部資料；兩者都在程式旁。可從「內容 → 加入本機資料夾」接入既有內容副本。原件不會因加入資料夾而自動複製；在該文件上編輯會保存到它所在的位置。

進階啟動可用 `--root <內容資料夾>` 與 `--data <資料資料夾>` 指定獨立位置。`PROMPT_STUDIO_DATA` 亦可指定資料位置；內容資料夾須放在資料目錄外。

## 先試一次完整操作

1. 從歡迎頁開啟「從筆記到 Prompt」，依序試海岸筆記、海岸素材與練習畫布。新增自己的 Markdown 筆記，輸入文字，再切換閱讀模式。
2. 新增 Prompt 分類與 Canvas 工作流，將文字素材拖入 Canvas。新建 Canvas 提供畫布、Prompt 輸出、CLIP 輸入、預排程、Stage 與圖片預覽六個已連接的模組。兩份文件可左右並排。
3. 收合內容側欄，將游標移向左側，確認它暫時展開；移開後會收回。設定 → 介面個人化 → 操作行為可調整此功能。
4. 關閉再重開文件，確認內容與工作區布局。Canvas 與分類沿既有保存流程自動保存。

要使用 ComfyUI，安裝同版配套擴充，並由你重新啟動後端與刷新工作流網頁。通用、legacy 與 modern 配套擇一；相容設定名稱不代表已完成真實 GPU 驗收。

## 更新前

保留舊程式、舊擴充及原資料。先以新的資料目錄和內容副本使用 0.8.9，不讓新舊程式同時寫入同一份資料。完整舊資料庫導入仍有驗證限制；這個啟動器不會自行轉換或刪除原安裝。
