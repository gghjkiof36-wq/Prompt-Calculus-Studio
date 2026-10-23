# Prompt Calculus Studio 0.8.9 Beta2

Beta2 修正保存、恢復、匯入與備份問題，並改善 Markdown 閱讀、搜尋與大型文件導航。

## 修復的內容

- **保存、恢復與關閉**：修正恢復稿被舊內容覆蓋、重試關閉出錯，以及收藏位置影響保存／另存的問題。輸入法仍在組字，或內容尚未成功保存且沒有恢復副本時，視窗保持開啟，保留稿件與 Undo。
- **匯入部分完成**：部分附件損壞或無法解壓縮時，保留已完成的對話，並顯示實際匯入結果。
- **備份取消與失敗**：新 ZIP 確認可讀後才替換舊備份；取消或失敗時保留可恢復的檔案，訊息中的恢復位置與完整內容可複製。
- **Markdown 閱讀與搜尋**：改善閱讀與搜尋的反應，修正大型文件末段無法正確捲動或定位游標的問題。
- **筆記資料夾**：新增或移除筆記資料夾後，閱讀頁會依目前範圍更新，避免顯示舊範圍內容；未保存的編輯保留。
- **收圖與另存**：減少重複收圖的處理，修正切換文件或另存後，收圖結果與收藏對應到錯誤文件的問題。
- **CivitAI**：修正一種預覽時退出的問題。更新或清除 Token 後，先前的搜尋與預覽結果不會蓋掉目前畫面。

## 下載與啟動

從[本次 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.9-beta.2)下載：

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.9-Beta2-source.zip` | 來源主程式、歡迎頁、教材、同版配套與安裝腳本 |
| `PCS-v0.8.9-Beta2-ComfyUI.zip` | 通用配套 |
| `PCS-v0.8.9-Beta2-ComfyUI-legacy-0.39.2.zip` | legacy 相容設定檔 |
| `PCS-v0.8.9-Beta2-ComfyUI-modern-0.40.0.zip` | modern 相容設定檔 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

本次沒有 Windows EXE 公開下載。來源版需要 Windows 64 位元 Python 3.12；完整解壓後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.9-beta.2
```

首次安裝依賴後可雙擊 `Start.cmd`。更新前保留舊程式、配套及原資料，以新資料夾和內容副本試用；搬移時同時保留 `files` 與 `data`。ComfyUI 配套三擇一，更新前關閉後端，完成後由你重新啟動並刷新工作流網頁。[開始使用](../../guide/workspace-089.md) · [配套相容](../../guide/comfyui-compatibility.md)。

## 使用前留意

- 大型文件、貼上及大量 JSON 匯入仍可能停頓。
- 替換備份時，原路徑可能短暫沒有檔案。若中途失敗，先從訊息中的恢復位置取回備份，確認可讀後再重試。詳見[備份與回退](../../guide/data-backup-privacy.md#備份與回退)。
- 舊版匯入輸出不會自動清理。重新匯入另存，保留手改筆記，分享前檢查原始紀錄與附件。
- 仍有異常退出情況。更新時先用資料副本試用；輸入法、螢幕縮放、舊資料與 ComfyUI 環境的相容限制見下方說明。

[已知問題與限制](validation/beta.2.md) · [資料與隱私](../../guide/data-backup-privacy.md) · [先前 Beta](beta.md)
