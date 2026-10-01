# Prompt Studio 0.5.0 驗收紀錄

驗收日期：2026-09-11。

## 已通過

- 50 項 Python 測試：原有桌面操作、獨立輸出排序、PNG 讀取、快照驗證與恢復、備份／取消、唯讀素材庫、來源檢查、重複收藏、同名編號、複製失敗清理。
- 5 項前端狀態測試：原文字保留、手動／空白稿、工作流序列化、連線與缺少欄位、不同節點互不影響、歷史素材保留。
- 獨立封裝 EXE：啟動檢查及 23 項選單／操作檢查通過。使用全新測試資料夾及 offscreen 模式。
- 實際本機 ComfyUI 0.21.1／前端 1.43.18：CPU 一像素測試工作流通過連續提交、上游節點快取、標準 Save Image PNG 寫入、原始位元組收藏與去重。
- 整個工作流（含 Save Image）命中快取：沒有新增 PNG，仍記錄當次獨立提交快照，既有圖片不改掛到新任務。
- 瀏覽器操作：綁定現有 text、選素材直接更新文字、直接手動編輯、清除手動稿、選擇收藏資料夾、收藏成功與元資料提示、刷新清單保留已收藏狀態。
- 瀏覽器先生成紅色洋裝 Prompt，再加入站姿：收藏舊 PNG 後讀回仍是原 Prompt；保存的工作流含新組合、節點綁定與目前控制節點。

## 交付

- `release/PromptStudio/PromptStudio.exe` 已更新；替換前後 `data/studio.sqlite3` 雜湊一致。
- 舊 EXE 與執行環境位於 `release/program-backups/before-0.5-20260911-225952`；另有一致的資料庫備份 `qa/before-050.sqlite3`。
- `release/PromptStudio-ComfyUI.zip` 為獨立擴充包。
- 擴充已安裝至本機 ComfyUI 的 `custom_nodes/comfyui_prompt_studio`，逐檔雜湊驗證通過；已指定桌面素材庫為唯讀來源。
- 生成測試與圖片全部使用專案 `qa/comfy-integration`；未使用使用者模型或執行 GPU 生圖。

首次使用須重新啟動正式 ComfyUI 後端並重新整理網頁。驗證範圍是本機標準 PNG 結果與最外層可編輯 text，不包括所有第三方節點或子圖。詳細操作見 `COMFYUI_GUIDE.md`。
