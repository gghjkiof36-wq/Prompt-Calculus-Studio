# Prompt Calculus Studio（PCS）

在本機整理 Markdown 筆記、Prompt 素材與 Canvas 工作流程。把常用提示詞保存成文字模塊、組合與流程，安排文字和圖片，再交給自己的 ComfyUI 生成；筆記編寫與 Prompt 整理也能單獨使用。

**化繁為簡，從混亂中找出秩序。**

[English](README.en.md) · [開始使用](docs/guide/workspace-089.md) · [完整文件](docs/README.md) · [0.8.9 更新說明](docs/releases/0.8.9/repair.1.md)

## 能做什麼

- **邊記錄，邊整理**：Markdown、Prompt 分類與 Canvas 開成分頁，左右並排；同一份筆記可一側編寫、一側閱讀。
- **保存可重用的素材**：文字模塊保存命名變體，組合安排文字與權重，流程決定順序與重複次數。
- **安排多筆 Prompt**：畫布提供本批固定文字，流程提供多輪內容；短來源循環，各使用位置保存自己的進度。
- **接上生成工作流**：CLIP 綁定文字欄位，Stage 選擇工作流與參數；可預排程、接續圖片、暫停及重試。
- **找回內容**：用大綱、全文搜尋、標籤、連結、書籤與版本紀錄整理本機筆記。

生成需要你自己的 ComfyUI、模型與工作流。編寫筆記、整理素材或複製 Prompt 不需要連接生成服務。

## 畫面預覽

### 筆記與 Prompt 素材並排

左邊記錄構圖與光線，右邊查看可加入 Canvas 的提示詞素材。

![0.8.9 筆記與 Prompt 素材並排](docs/images/workspace-split-0.8.9.png)

### Markdown 原文與閱讀

同一份文件在兩側顯示，圖表、表格與公式保留可編輯的原文。

![0.8.9 Markdown 原文與閱讀](docs/images/markdown-source-reading-0.8.9.png)

### 大綱與內容側欄

用標題跳到段落；收合內容側欄後，游標靠近左側可暫時展開，展開與收回延遲可在設定調整。

![0.8.9 Markdown 大綱](docs/images/markdown-outline-0.8.9.png)

![0.8.9 自動展開內容側欄](docs/images/sidebar-peek-0.8.9.png)

## 下載與啟動

目前版本為 **[0.8.9 Alpha 1 Repair 1](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-alpha.1.repair.1)**。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Alpha1-Repair1-source.zip` | 來源主程式，內含歡迎頁、教材、同版配套與安裝腳本 |
| `PCS-v0.8.9-Alpha1-Repair1-ComfyUI.zip` | 通用擴充，供手動安裝 |
| `PCS-v0.8.9-Alpha1-Repair1-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.9-Alpha1-Repair1-ComfyUI-modern-0.40.0.zip` | 兩個相容設定檔，與通用版擇一安裝；版本對照見[相容指南](docs/guide/comfyui-compatibility.md) |
| `SHA256SUMS.txt` | 下載完整性校驗 |

本次公開來源版，**沒有 Windows EXE 公開下載**。來源版需要 Windows 64 位元 Python 3.12。完整解壓後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-alpha
```

首次安裝會下載依賴，之後雙擊 `Start.cmd`。要生成圖片，另安裝同版 ComfyUI 配套，再重新啟動後端與刷新工作流網頁。[完整步驟](docs/guide/workspace-089.md)。

## 第一次使用

首次啟動會開啟「歡迎使用.md」，內有操作介紹與教學入口。從「教學素材」跟著海岸筆記、文字素材、組合與流程練習，再把素材副本加入 Canvas。新建 Canvas 預設有六個已連接的模組，可從畫布一路接到圖片預覽；關閉歡迎頁後，重新啟動會接續原來的分頁安排。新文件預設在程式旁的 `files`，設定在 `data`；加入其他本機資料夾後，編輯會保存到文件原位置。

## 使用前留意

- 更新前保留舊程式、配套與原資料，先用獨立資料目錄和內容副本試用。舊資料庫導入仍有相容性待確認，新舊程式不要同時寫入同一份資料。
- 接近 8 MiB、段落很多的 Markdown 文件，首次切換閱讀仍可能短暫停頓；複雜內容完整呈現可能需要數秒。
- 不同輸入法、螢幕縮放、多螢幕、媒體格式與 ComfyUI／GPU 環境尚未逐一驗證，詳見[已知限制](docs/releases/0.8.9/validation/repair.1.md)。
- 生成 PNG 保留必要 Prompt 與還原參照，完整備份包含私人素材；分享前先[檢查附加資訊](docs/guide/data-backup-privacy.md#分享前)。

[歷史發布](docs/releases/README.md) · [Issues](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/issues) · [貢獻指南](CONTRIBUTING.md) · [安全回報](SECURITY.md)

## 授權

[AGPL-3.0-only](LICENSE)
