# Prompt Calculus Studio 0.8.5 Alpha 1

發布日期：2026-10-02（Asia/Taipei）。

這版讓你直接在 Stage 設定工作流參數，並為每筆等待任務保存不同的值，方便比較結果。

## 新增

- **編輯每一步的參數**：雙擊 Stage，搜尋並選取節點，修改 CFG、步數、尺寸或種子，檢視變更後套用。套用只保存設定，按執行才生成。
- **比較多組設定**：兩筆等待任務可分別保存 CFG 3／4；後續改一般 Stage，不會改掉已保存的任務設定。沒有指定的欄位仍在執行前讀取 ComfyUI。

## 改善

- **改善參數面板**：分類可收合，種子數值與模式放在同一欄；改善捲動、長選單搜尋、短選單留白及浮點數顯示。

## 修正

- **修正操作問題**：修正部分普通欄位誤判為唯讀、JSON 匯入後畫布未更新、放大模型選單，以及 Ultimate SD Upscale 的原生種子控制。

## 已知問題

- **部分參數仍是唯讀**：已接線、由 PCS 綁定或特殊控制的欄位，請從真正供值的來源或 ComfyUI 原生網頁修改。更新後仍唯讀時，先確認同版擴充並重啟服務、重新整理網頁；不需手動重建工作流。
- **種子編輯有範圍限制**：PCS 上限為 `1125899906842624`（2^50），超過範圍的既有值保留唯讀，請在 ComfyUI 設定；多組種子或特殊控制亦可能需在原生網頁操作。
- **套用前原生值被改動**：核對 ComfyUI 中的新值，重新開啟面板比較再設定。已開始準備或提交的任務不能再改參數，請等它結束後建立新項。
- **工作流一直載入或圖片未顯示**：先到 ComfyUI 查原任務，避免再次生成；保存工作流後重新整理網頁，圖片取回失敗可從任務紀錄重取。更多處理見[疑難排解](../../guide/troubleshooting.md)。

## 升級注意事項

更新前備份資料，將新版放到新資料夾並使用資料副本，保留舊主程式、擴充與資料。未完成流程重開後先暫停，確認內容再繼續；舊項沒有參數設定時，不會自動採用當前 Stage 的值。

## 下載與使用方式

從[本次 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.5-alpha.1)下載：

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.5-Alpha1-source.zip` | PCS 原始碼版主程式 |
| `PCS-v0.8.5-Alpha1-ComfyUI.zip` | 同版 ComfyUI 擴充 |
| `SHA256SUMS.txt` | 檢查下載內容是否完整 |

本次提供原始碼版，不提供 Windows EXE 下載。請先安裝 Windows 64 位元版 Python 3.12，解壓來源包，在含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.5-alpha
```

首次安裝需要網路下載套件，完成後可雙擊來源包內的 `Start.cmd`。要生成圖片，請安裝同版擴充、重啟 ComfyUI 並重新整理網頁，保持 PCS、ComfyUI 與工作流網頁開啟。完整步驟見[開始使用](../../guide/getting-started.md)、[ComfyUI 整合](../../guide/comfyui.md)及[Stage 參數指南](../../guide/stage-parameters.md)。

## 授權

AGPL-3.0-only
