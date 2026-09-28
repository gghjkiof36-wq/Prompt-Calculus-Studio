# ComfyUI 整合

適用：0.83 Alpha 1。先確認本機 ComfyUI 與工作流能正常生成；本版需要保持該工作流網頁開啟。

## 安裝配套擴充

從 [同版 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.83-alpha.1) 取得 `PCS-v0.83-Alpha1-ComfyUI.zip`，解壓到 ComfyUI 安裝目錄以外。完整資料夾名稱為 `comfyui_prompt_calculus_studio`；不能只複製開發原始碼中的 `comfyui_prompt_studio`，它缺少打包時加入的共用核心。

先啟動一次 PCS 建立資料庫，再關閉 ComfyUI。使用完整擴充包內的安裝腳本，將範例路徑改成實際位置：

```powershell
& 'D:\Downloads\PCS-extension\comfyui_prompt_calculus_studio\install_comfyui.ps1' `
  -Source 'D:\Downloads\PCS-extension\comfyui_prompt_calculus_studio' `
  -ComfyUIRoot 'D:\ComfyUI' `
  -DesktopData 'D:\Apps\PCS\data'
```

已有舊擴充時，確認 ComfyUI 已停止後加 `-Update`。腳本核對完整包與桌面資料庫，將舊擴充保存在 ComfyUI 的 `.pcs-program-backups`；若新舊名稱同時存在或 `DesktopData` 與舊設定不同，會停止而不覆寫設定。先保存並移出不使用的擴充、確認正確資料位置，再重試。不要刪除資料庫或憑證來繞過錯誤。

安裝後重新啟動 ComfyUI 後端並重新整理網頁；舊網頁仍可能使用舊協定。此指南以可管理 `custom_nodes` 的本機安裝為準，官方 ComfyUI Desktop 尚未完成本版相容性驗收。

開發者從完整 Git checkout 建包可執行 `py -3.12 build_comfyui.py --v083-direct`，預設輸出 `release/PromptCalculusStudio-ComfyUI.zip`，且需要全新輸出目錄。發布來源 ZIP 不含 Git 中繼資料，請直接使用同版擴充附件，不把自行建包當成使用前提。

## 第一次連接與綁定

1. 開啟 ComfyUI 網頁與要使用的工作流。PCS 設定中連接實際本機網址，例如 `http://127.0.0.1:8188`；擴充資料設定指向含 `studio.sqlite3` 的桌面資料夾。
2. 在 PCS 的 CLIP 綁定視窗選擇同一原生工作流與可編輯的文字節點／欄位。不要用同名工作流副本代替原生身分。欄位已被其他 CLIP 綁定時，先解除原綁定。
3. 選「使用 PCS 提示詞」（預設）或「使用 ComfyUI 手動文字」。PCS 模式會把該綁定的桌面文字寫入網頁；手動模式保留網頁文字，包括刻意清空。
4. 確認文字來源與目標正確，保持該工作流網頁開啟，再從 PCS 執行一次。沒有唯一對應網頁、版本不符或綁定失效時，依提示修正後再提交。

文字由上游節點供應時，選可直接編輯的上游來源；不要把所有第三方節點、子圖或欄位視為已支援。

## 直接執行與預排程

沒有接預排程時，每次按執行立即提交至 ComfyUI 原生佇列，不等待上一張完成；次數設 3 就提交 3 次。各次使用點擊當時的文字與圖片，提交交接期間再修改的內容屬下一次點擊。

接上預排程且空閒時，直接提交目前內容；正在生成時再點擊，才保存通過預排程端口的內容，完整完成後自動接續。未經預排程的負面文字、圖片或其他欄位在派送時取值。要固定圖文配對，圖片和文字都要通過同一份預排程。ComfyUI 的 seed hooks 在真正提交時生效。

資料流為：點擊取得本次輸入，或忙碌時保存預排程的接入資料 → 派送時補上未接入欄位 → 配套網頁套用綁定 → 原生序列化及 queuePrompt → 服務回覆與 queue/history 完成核對。生成紀錄與原圖資料不能以後來修改的 Prompt 補寫。

底欄只有次數、執行、× 取消、任務數。預排程暫停、繼續、排序及內容編輯在模塊內，點任務數查看紀錄。ComfyUI 原生 Run 保留自己的操作，不領取或等待 PCS 預排程；PCS 暫停、關閉或舊未確認紀錄不會鎖住原生按鈕。

## 圖片來源與預覽

Canvas 的圖片來源支援單張、多張、資料夾及最近生成選取；圖片輸入指定 LoadImage 接收節點，與 PreviewImage／SaveImage 輸出節點分開。只使用圖片而不接文字的流程可使用圖片輸入，不再沿用 Repair 5 必須有 CLIP 的限制。

未接預排程時，依點擊次數提交對應圖片。接上預排程時可啟動整批供應，最多十項（含提交中／生成中及等待），每完整完成一項再補入。這是同一目標工作流的圖片供應，跨工作流自動串接未納入。單張可重用，有限集合走到底不自動循環。

整批輸出可並排／網格預覽；點圖只放大，不改加載選擇。未接線預覽清空，「最近生成」獨立保留。更多圖片文字來源與操作見 [操作指南](docs/USER_GUIDE.md)。

本輪仍要求 PCS、ComfyUI 服務及網頁開啟，不支援關頁執行。桌面與擴充必須同版，版本及驗證界線見 [QA 摘要](docs/validation/083_DIRECT.md)。

## 收藏、快照與停止

在最近生成選擇收藏位置後保存結果。PNG 可能保有工作流與模組快照；是否存在依實際輸出而定。缺少 Metadata、第三方儲存節點或舊圖片可能沒有完整資料，不能補寫目前 Prompt 當歷史。

含快照的圖片可恢復其模組組合，無法自動安裝缺少模型或重建完整環境。取消只核對並操作指定 PCS 工作，不清空 ComfyUI 佇列或取消無關任務；普通模式其他已提交項目留在原生佇列，預排程未提交內容保留並暫停。提交結果不明時先查看 ComfyUI 歷史，避免手動重複執行。

## 相容與資料

桌面及擴充使用同一版本；舊的 `prompt_studio` 模組、PNG 欄位、環境變數與資料識別保留相容用途。擴充設定與歷史位於 ComfyUI 的 `user/prompt_studio`，另有指向桌面資料庫的 `local_library.json`。更新時備份擴充及資料；回退使用舊程式、舊擴充與舊資料副本，勿覆蓋新資料。詳見 [資料與隱私](docs/DATA_AND_PRIVACY.md)。
