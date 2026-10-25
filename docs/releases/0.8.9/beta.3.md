# Prompt Calculus Studio 0.8.9 Beta3

把 ChatGPT 對話帶進本機筆記，保留公式、引用與附件，再整理成可編輯、可攜帶的文件。

[開始使用](../../guide/workspace-089.md) · [Chrome 設定](../../guide/chrome-connector.md) · [保存與匯出](../../guide/markdown-retention.md)

## 把對話帶進 PCS

Chrome 擴充可處理 ChatGPT 網頁中的單則或多則訊息。選取後，直接移到「儲存到 PCS」選資料夾，或下載 JSON／Markdown。完成後不再彈出整篇對話預覽，也不鎖住下一次儲存。單則選單保留三個動作；全頁入口另有下載紀錄。

PCS 的下載紀錄按日期顯示，每筆可開啟文件、顯示資料夾或移除紀錄。移除紀錄不會刪除文件。Chrome 擴充與連線工具另提供配套下載，從下載紀錄頁按「加入 Chrome」設定；目前仍需在 Chrome 的擴充功能頁載入未封裝項目。

## 筆記、公式與文件

- 改善 `$…$`、`$$…$$` 及 ChatGPT 常用數學分隔形式，保留積分、微分、根號、求和、矩陣、上下標、工程數字及單位。
- 改善行高、段落間距、連結符號與常用表格。引用可保留來源連結，支援的引用資料在 PCS 顯示成可懸停查看的卡片。
- 已取得的圖片與可轉成靜態圖像的內容可隨文件保存。缺少圖片、來源失效或不支援的互動內容會保留可用資訊，不憑空補圖。
- 編寫、原文與閱讀沿用目前選擇，切換文件不再跟著各頁舊狀態跳動。檔案樹縮排反映實際層級。
- 搬運文件遇到同名會加上 `(n)`。原檔被外部刪除且仍有可讀草稿時，可從缺檔頁或首頁另存未儲存內容。

原有的演示附件、問答來源與分支、人工 ChatGPT 往返、資料夾／ZIP 可攜輸出繼續保留。[操作指南](../../guide/markdown-retention.md)說明這些工具的使用方式。

## 素材與日常使用

來源包內含原五份海岸素材，以及新增六類共 54 份文字、組合與流程素材；另附八份筆記模板、公式與單位參考、Markdown 相容性測試文件。文件都能在 PCS 裡編輯，素材拖入 Canvas 後是可獨立修改的副本。

在「設定 → 介面個人化 → 操作行為」可選擇畫面動畫：關閉、櫻花、下雨或雪花。預設關閉，效果稀疏且不接收滑鼠或鍵盤輸入。縮小、切到背景，或啟用減少動態效果時會暫停。

收合或展開側欄後，動畫會繼續覆蓋文件區，不需要切換頁面恢復；雨滴速度稍微加快。工作區設定移除舊版「分開保存畫布與清單的選擇」，各文件仍保留自己的內容。加入素材的管理頁清單只顯示存在且類型相符的檔案，已移走但仍開著的頁面不再混入來源清單。

更新前保留舊程式、配套與 `files`／`data`，先用副本試用；新舊版本不要同時寫入相同資料。來源版可用 `--root` 與 `--data` 指定原位置，詳見[啟動與沿用資料](../../guide/workspace-089.md)。新增教材留在新包，需要時再複製。

## 下載與啟動

從 [Beta3 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta.3) 下載：

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Beta3-source.zip` | 來源主程式、教材、素材、模板與同版 ComfyUI 配套 |
| `PCS-v0.8.9-Beta3-Chrome-Connector.zip` | Chrome 0.2.7 擴充與 Windows 連線工具 |
| `PCS-v0.8.9-Beta3-ComfyUI.zip` | 通用 ComfyUI 配套 |
| `PCS-v0.8.9-Beta3-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.9-Beta3-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

PCS 主程式提供來源版，沒有 PCS Windows EXE 公開下載。安裝 Windows 64 位元 Python 3.12，完整解壓來源包後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta.3
```

首次安裝依賴後可雙擊 `Start.cmd`。Chrome 配套解壓後放到程式旁的 `connector-package`，再從 PCS「探索 → 下載紀錄 → 加入 Chrome」完成連接與載入；詳見[連線步驟](../../guide/chrome-connector.md)。生成圖片另需自己的 ComfyUI、模型與工作流，以及一份同版[ComfyUI 配套](../../guide/comfyui-compatibility.md)。

## 目前限制

極長網頁對話仍以實際載入的訊息為範圍；「全選」不能保證包含網站尚未載入的歷史。複雜網頁表格、互動圖表與小工具不保證原樣重現，部分內容只保留靜態圖像、文字或來源連結。已經在舊貼上過程中遺失的公式字元，不能只靠顯示修復還原。

一般 Markdown 與 Obsidian 常用格式已納入相容性檢查，但不代表支援所有 Obsidian 外掛。ChatGPT 整理採人工複製與貼回，本版不自動執行 LLM。更新前請先用資料副本試用，並確認自己的輸入法、顯示與連線環境可正常操作。

[本版限制](validation/beta.3.md) · [資料與隱私](../../guide/data-backup-privacy.md) · [先前 Beta2](beta.2.md)
