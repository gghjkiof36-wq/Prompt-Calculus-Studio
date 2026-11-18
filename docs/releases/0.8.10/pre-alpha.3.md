# Prompt Calculus Studio 0.8.10 Pre-alpha3

這版整理 Canvas 的排程、生成結果與圖片預覽。再次按執行可排入下一批；生成完成後，原圖在背景收齊，再依順序一起顯示。也修正 PCS 與 ComfyUI 中斷後的接續與結果保存。

[開始使用](../../guide/workspace-0810.md) · [Canvas 與更新步驟](../../guide/canvas-prealpha3.md)

## 排程與生成

- 每次執行建立一組固定輸入，畫面直接顯示項目數。生成中可繼續排入下一批。
- 修正保留項目占住準備額度，導致新批次卡住的問題。暫停、繼續、取消與工作狀態依擴充回覆更新。
- ComfyUI 以事件通道回報狀態，PCS 在重新連線後查回原工作並接續。
- 生成完成後在背景收取原圖，全部到齊才一起保存。PCS 中斷或後端重開後，可從擴充保存的結果繼續收取。

## 畫布與圖片

- 減少大量排入、保存與狀態更新時的重複處理。
- 大批來源卡、結果卡與清單圖片改在背景解碼；生成佇列只重讀有變動的縮圖。
- 圖片來源統一連到「ComfyUI 圖片輸入」。從圖片選「用這張圖當來源」即可接上，匯入工作流時直接辨識所需輸入。
- 關閉視窗時等待背景工作完成，並提供掉幀紀錄供問題排查。

## 安裝與更新

從 [Pre-alpha3 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.3) 取得來源包，以及需要的配套。

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.10-Pre-alpha3-source.zip` | 主程式來源、教材、素材、模板與同版 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha3-ComfyUI.zip` | 通用 ComfyUI 擴充 |
| `PCS-v0.8.10-Pre-alpha3-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.10-Pre-alpha3-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一 |
| `PCS-v0.8.10-Pre-alpha3-Chrome-Connector.zip` | Chrome 0.2.7 擴充及 Windows 連線工具 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

主程式使用 Windows 64 位元 Python 3.12。完整解壓來源包，在 `run.py` 旁開 PowerShell 建立環境，之後雙擊 `Start.vbs`。

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

**生成需要更新同版 ComfyUI 擴充。** 關閉 ComfyUI，在「設定 → 管理與更新 → PCS 擴充」安裝，再重啟後端及重新整理工作流頁面。可使用 ComfyUI Desktop 或瀏覽器中的工作流，連接自己的實際服務網址。[連線步驟](../../guide/comfyui.md)。

**舊資料請先備份，再用複製後搬遷功能建立新版資料。** 新版資料庫供本版使用，回退時開保留的舊資料副本。[搬遷步驟](../../guide/canvas-prealpha3.md#沿用舊資料)。

## 使用前留意

部分大型操作仍會卡頓，背景存檔、局部刷新及其他畫面優化會接續處理。本機 Agent 的回答和引用仍需覆核。模型與推理引擎需自行準備，來源、對話與圖片分享前請檢查私人內容。

[驗證紀錄](validation/pre-alpha.3.md) · [本機模型](../../guide/local-ai.md) · [歷史版本](README.md)
