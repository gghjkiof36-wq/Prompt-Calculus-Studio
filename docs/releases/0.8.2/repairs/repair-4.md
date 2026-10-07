# 0.8.2 Alpha 1 Repair 4

所屬版本：0.8.2 Alpha 1，Repair 4 隔離候選；基準 Repair 3 `4891465`，歷程含 `d066ecf`、`7b6a9b8`。

## 問題與影響

API 重導、來源包收檔、圖片來源與手寫保護、刪除復原的殘留選擇，以及安裝／回退核對有缺口。

## 修正內容

- API 重導沿用來源保護：HTTPS 不降級，換主機或埠不轉送 Authorization；同來源正常使用。
- 來源 ZIP、外層文件與擴充程式只收 Git 已登記且符合允許範圍的檔案；本機 bootstrap、資料庫、憑證與 log 不收。前端 CSS 保留。
- 图片對照完整 path／native 身分，不能由相同 PCS ID 略過其他來源資料；同一路徑多個網頁身分無法分辨時拒絕猜圖。A→B 指定 prompt_id 仍讀凍結結果。
- 修正獨立複驗發現的空來源回歸：桌面匯入工作流的圖片查詢可攜帶 `origin: null`，與未提供來源同義；歷史結果也適用。其他錯誤來源型別明確拒絕，不跳過 path／native／伺服器核對。原 d066ecf 候選保留作歷史證據，後續固定提交見交付索引。
- 同一網頁 node/widget 的手動文字保護不因 owner 或 CLIP binding 名稱改變而消失。僅觀察到空綁定才釋放的既有行為保留，不宣稱可靠解除重綁接管或雙向編輯。
- 刪除工作流清掉指向該工作流的 CLIP 選擇，保留 CLIP、Prompt、B 的選擇及排序。復原先驗候選，缺 CLIP／衝突時保留 live state、文件與日誌。文件与日誌一起提交；遠端操作與 SQLite 分開，遠端成功但本機失敗會保留實際結果提示。
- 原 CLIP 選擇的持久恢復已依使用者於 00 核准的 02 ADR aada3d5 實作：刪除日誌額外保存可選 `clip_workflows`，只記刪除當時選定該工作流的 CLIP。保存重開後可復原原選擇；原本選 B 的 CLIP 即使另有 A 綁定也保持 B。固定增量交付後由 04／05 獨立驗收，不能把本組自驗當成整批完成。
- 清單刪除及縮放右鍵定位沿用 Repair3 修復，已重跑其回歸；同 ID profile 參數政策、診斷入口細節及可靠接管語義保留現況，詳見 082_REPAIR4_INVENTORY.md。

### 安裝與包核對

PACKAGE_MANIFEST.json 列出交付程式包的所有檔案（自身除外），含 EXE、runtime、文件及來源證據。SOURCE_MANIFEST.json 只列專案原始碼，不能替代二進位清單。BUILD_INFO.json 記同一來源提交及 binary_git_head；SHA256 是一致性檢查，不是簽章或來源可信度認證。

桌面：解壓後可用同包啟動器，它只使用同目錄的新 data。update_desktop.ps1 接受明確 Source、Destination，目的地必須全新且父目錄存在；先驗來源及 staging，才整包配置。既有目的地直接拒絕，不自動搬資料或改舊捷徑。

擴充：先關閉 ComfyUI，將候選 ZIP 解壓至 ComfyUI 以外的目錄，再執行同包 install_comfyui.ps1，明確指定 Source（comfyui_prompt_calculus_studio）、ComfyUIRoot、DesktopData；已有擴充時需 -Update。DesktopData 必須已有 studio.sqlite3，與舊 local_library.json 不一致會拒絕。來源缺失、hash 不符、reparse、同時存在新舊兩個名稱均不寫入。唯一舊擴充移到 ComfyUI/.pcs-program-backups/previous-*，位於 custom_nodes 掃描外；新目錄保留原 local_library.json 完整位元組，舊多餘程式檔不殘留。脚本不代停程序、下載依賴或操作 GPU。

若 staging／配置失敗，舊版及完整 staging 分開保留，不報成功；確認結果後才能重啟 ComfyUI、刷新網頁。本輪只執行合成樹上的安裝測試，沒有更新使用者真實安裝。

### 回復與來源重建

資料契約：新 `workflow_deletions.body` 紀錄含 `clip_workflows: {"clip_id": "workflow_id"}`，沒有原選擇時明確寫空物件。映射最多 100 項，鍵值須為非空字串，值須等於被刪 profile 的 ID；復原必須仍有原 CLIP、目前選擇為空，且對應文字節點／欄位有效。已改選其他工作流、CLIP 已刪、目標占用或紀錄損壞時，整個本機候選拒絕，原文件和日誌不被消耗。只保存映射，不保存完整 state；SQLite 表、document／snapshot 版本及同步協定不變。

舊紀錄缺字段時保留既有合法 profile／binding 復原，不推測 CLIP 選擇並提示確認；新空物件不提示資訊遺失。既有日誌不批次遷移。此最小映射只判斷目前狀態，不能區分期間曾改選再清空，亦不提供 CLIP 實例世代／ABA 保護。遠端已完成的移動仍依現有分步記錄處理，不宣稱 SQLite 可回退外部操作。

回復桌面時開保留的 Repair3 入口，搭配升級前舊資料副本；新資料留在新版，不能讓舊版直接讀新資料。擴充回復需先關閉 ComfyUI，把新擴充移出 custom_nodes，再將 .pcs-program-backups/previous-* 中的舊包移回 rollback JSON 記錄的 previousName，確認 local_library 指向該舊資料。不要同時載入新舊名稱。Git 回退不能替代資料副本。

Repair3、d066ecf 與 7b6a9b8 沒有新映射的完整恢復邏輯；不能因 JSON 可解析就把新版資料交給舊 EXE。升級及回復一律用各自獨立資料副本，原資料 hash 保留，新選擇紀錄與新編輯留在新版，不能刪字段後覆蓋回舊資料。

來源包不包含 .git、第三方 runtime 或私人資料。從來源 ZIP 重建時，先在全新目錄初始化 Git，檢視後登記來源並提交，備妥對應 Python／Qt／PyInstaller；建置器不接受無 Git 的任意目錄，亦不以遞迴收檔作退路。法律文字的上游位元組由 .gitattributes 保留；依賴附件與仍未知的來源見 THIRD_PARTY_REPAIR4.md。

## 驗證結果

此文件隨來源封裝，後續实际 EXE／包 hash、回復結果及固定測試證據列在 build/082_DELIVERY.json；文件存在不代表驗收已通過。04／05／06／08 獨立複驗另列，不沿用舊包結論。實際 ComfyUI 安裝與網頁渲染、原生多 DPI、GPU、當時卡死觸發及新機安裝仍未驗證。

## 收錄與發布

0.8.2 Repair 5 公開日期為 2026-09-27。本次為隔離候選，沒有獨立 GitHub Release。`d066ecf` 與 `7b6a9b8` 不含後來新增的完整 CLIP 選擇恢復映射，不能混用驗收結論。0.8.2 後續公開的是[Alpha 1 Repair 5](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.2-alpha.1.repair.5)。
