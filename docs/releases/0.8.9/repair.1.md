# 0.8.9 Alpha 1 Repair 1：從歡迎頁開始

[下載](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-alpha.1.repair.1) · [開始使用](../../guide/workspace-089.md) · [已知限制](validation/repair.1.md) · [0.8.9 完整功能](alpha.1.md)

## 這次更新

- 首次啟動會開啟「歡迎使用.md」，把功能介紹、常用操作與教學入口放在程式裡。
- 附上可編輯的教學素材：海岸筆記、三份文字素材、一份組合、一份流程，以及練習 Canvas。可跟著練習分頁、搜尋、拖放與保存。
- 新建 Canvas 提供六個已連接的模組：畫布 → Prompt 輸出 → CLIP 輸入 → 預排程 → Stage → 圖片預覽。先放入自己的素材，再設定工作流與綁定。
- 重新啟動會接續保存的分頁；關閉歡迎頁後不會強制插回。既有文件保持原內容。

## 下載與啟動

下載 `PCS-v0.8.9-Alpha1-Repair1-source.zip`，完整解壓到新資料夾。來源版需要 Windows 64 位元 Python 3.12；在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-alpha
```

首次安裝依賴後，可雙擊 `Start.cmd`。歡迎頁與教材在程式旁的 `files`，設定與工作區記錄在 `data`。教材可直接編輯，也可先複製再練習。

只寫筆記、整理提示詞或編排 Canvas 可以離線使用。要生成圖片，還需要自己的 ComfyUI、模型與工作流；通用、legacy、modern 三份同版配套擇一安裝，再重新啟動後端與刷新網頁。參考[安裝步驟](../../guide/workspace-089.md)與[相容指南](../../guide/comfyui-compatibility.md)。

## 使用前留意

- 本次提供來源主程式、三份 ComfyUI 配套與 SHA256 校驗檔，沒有 Windows EXE 公開下載。
- 更新前保留舊程式、配套及原資料，在新資料夾以副本試用。新舊程式不要同時寫入同一份資料；完整舊資料庫導入仍有相容性待確認。
- 大型 Markdown 首次閱讀仍可能短暫停頓，複雜內容可能需要數秒。不同輸入法、螢幕縮放、媒體與 ComfyUI／GPU 環境尚未逐一驗證。

[詳細限制](validation/repair.1.md) · [原 Alpha 1 下載](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-alpha.1)
