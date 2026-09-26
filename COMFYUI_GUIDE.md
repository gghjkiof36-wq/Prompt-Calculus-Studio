# ComfyUI 整合

適用：0.82 Alpha 1 Repair 5。先確認本機 ComfyUI 與工作流能正常生成；本版需要保持該工作流網頁開啟。

## 安裝配套擴充

從 [同版 Release](https://github.com/gghjkiof36-wq/Prompt-Culculus-Studio/releases/tag/v0.82-alpha.1-repair.5) 取得 `PCS-v0.82-Alpha1-Repair5-ComfyUI.zip`，解壓到 ComfyUI 安裝目錄以外。完整資料夾名稱為 `comfyui_prompt_calculus_studio`；不能只複製開發原始碼中的 `comfyui_prompt_studio`，它缺少打包時加入的共用核心。

先啟動一次 PCS 建立資料庫，再關閉 ComfyUI。使用完整擴充包內的安裝腳本，將範例路徑改成實際位置：

```powershell
& 'D:\Downloads\PCS-extension\comfyui_prompt_calculus_studio\install_comfyui.ps1' `
  -Source 'D:\Downloads\PCS-extension\comfyui_prompt_calculus_studio' `
  -ComfyUIRoot 'D:\ComfyUI' `
  -DesktopData 'D:\Apps\PCS\data'
```

已有舊擴充時，確認 ComfyUI 已停止後加 `-Update`。腳本核對完整包與桌面資料库，將舊擴充保存在 ComfyUI 的 `.pcs-program-backups`；若新舊名稱同時存在或 `DesktopData` 與舊設定不同，會停止而不覆寫設定。先保存並移出不使用的擴充、確認正確資料位置，再重試。不要刪除資料庫或憑證來繞過錯誤。

安裝後重新啟動 ComfyUI 後端並重新整理網頁；舊網頁仍可能使用舊協定。此指南以可管理 `custom_nodes` 的本機安裝為準，官方 ComfyUI Desktop 尚未完成本版相容性驗收。

開發者從完整 Git checkout 建包可執行 `py -3.12 build_comfyui.py --v082-alpha1-repair5`，預設輸出 `release/PromptCalculusStudio-ComfyUI.zip`，且需要全新輸出目錄。發布來源 ZIP 不含 Git 中繼資料，請直接使用同版擴充附件，不把自行建包當成使用前提。

## 第一次連接與綁定

1. 開啟 ComfyUI 網頁與要使用的工作流。PCS 設定中連接實際本機網址，例如 `http://127.0.0.1:8188`；擴充資料設定指向含 `studio.sqlite3` 的桌面資料夾。
2. 在 PCS 的 CLIP 綁定視窗選擇同一原生工作流與可編輯的文字節點／欄位。不要用同名工作流副本代替原生身分。欄位已被其他 CLIP 綁定時，先解除原綁定。
3. 選「使用 PCS 提示詞」（預設）或「使用 ComfyUI 手動文字」。PCS 模式會把該綁定的桌面文字寫入網頁；手動模式保留網頁文字，包括刻意清空。
4. 確認文字來源與目標正確，保持該工作流網頁開啟，再從 PCS 執行一次。沒有唯一對應網頁、版本不符或綁定失效時，依提示修正後再提交。

文字由上游節點供應時，選可直接編輯的上游來源；不要把所有第三方節點、子圖或欄位視為已支援。

## 文字、參數與生成

一次 PCS 執行對應一次目前原生工作流提交，ComfyUI 自己的 `batch_size` 可以大於 1。尺寸、採樣器、Seed 與其他未綁定參數沿用網頁當前值。PCS 多輪、跨工作流順序執行及無網頁背景回退尚未完成；已保存的工作流順序不代表本版會依序執行。

工程資料流為：PCS 凍結本次文字／選定圖片 → 配套網頁套用明確綁定 → ComfyUI 原生 queuePrompt、序列化與 seed hooks → 後端核對提交內容。本文只概述已實作路徑；模擬回歸與使用者人工校驗的範圍見 [QA 摘要](docs/validation/082_REPAIR5.md)。排隊後的任務與圖片歷史不應以後來修改的文字冒充。

## 圖片輸入與整批預覽

在 CLIP 綁定視窗選同一工作流的 LoadImage 目標，將圖片接到 Prompt 後選定要送入的一張。只有存在圖片接線時才套用圖片。本版仍需至少一個有效 CLIP 綁定。

生成結果可整批並排／網格顯示；點圖只放大，不改變加載節點的選擇。未接線預覽會清空並提示連接來源；「最近生成」另行保留。網頁直接生成的結果依原生工作流身分匹配，較晚抵達的舊縮圖不應覆蓋最新完成批次。

整批預覽與下游逐張執行是不同能力。將整批按原輸出順序逐張送往下游仍待實作，目前圖片輸入須明確選定單張。

## 收藏、快照與停止

在最近生成選擇收藏位置後保存結果。PNG 可能保有工作流與模組快照；是否存在依實際輸出而定。缺少 Metadata、第三方儲存節點或舊圖片可能沒有完整資料，不能補寫目前 Prompt 當歷史。

含快照的圖片可恢復其模組組合，無法自動安裝缺少模型或重建完整環境。停止／清除佇列可能影響同一 ComfyUI 後端的其他任務，執行前核對當前佇列。提交結果不明時先查看 ComfyUI 歷史，避免手動重複執行。

## 相容與資料

桌面及擴充使用同一版本；舊的 `prompt_studio` 模組、PNG 欄位、環境變數與資料識別保留相容用途。擴充設定與歷史位於 ComfyUI 的 `user/prompt_studio`，另有指向桌面資料庫的 `local_library.json`。更新時備份擴充及資料；回退使用舊程式、舊擴充與舊資料副本，勿覆蓋新資料。詳見 [資料與隱私](docs/DATA_AND_PRIVACY.md)。
