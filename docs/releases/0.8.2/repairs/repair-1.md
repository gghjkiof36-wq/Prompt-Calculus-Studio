# 0.8.2 Alpha 1 Repair 1

所屬版本：0.8.2 Alpha 1，Repair 1；2026-09-14 的本機交付紀錄。

## 問題與影響

模型篩選文字裁切、工作流選擇、選單外觀及包名稱不一致。

## 修正內容

- 工作流管理保留搜尋、資料夾、匯入、重新讀取、重新命名、刪除與復原；0.8.2 不再顯示「設為活動工作流」。執行對象由各 CLIP 綁定及已核准的共同順序決定。
- 在 CLIP 點選工作流，可直接列出桌面已連結及 ComfyUI 已儲存的工作流，選定後讀取可綁定節點。只載入所選工作流，不整批匯入。按儲存才保存工作流和綁定，可一起 undo／redo；加載圖片共用同一入口，仍可不綁定。
- 連線失敗可重新整理再讀取。取消、切換選項、切換工作區或連線後拒絕舊回應；讀取與刷新不發起生成。相同工作流的正負面 CLIP 共用一份工作流，不能重複占用同一文字欄。
- 下拉清單取消常駐選取高光與內框，游標移入項目才顯示淡背景，鍵盤選擇功能保留。
- 模型篩選框在字型／樣式更新完成及顯示頁面時重新測量，依字體寬度換行。原截圖的特定裁切條件尚未完整重現；已修復可確認的高度更新時序缺口，不能據此宣稱已完成原生多 DPI 驗收。

一般 ComfyUI 畫布 JSON 沿用既有已知節點轉換。無法安全辨識的自訂節點會顯示 API 格式提示；可使用 ComfyUI 匯出的 API JSON 或 PCS 工作流交換檔。沒有猜測未知節點的欄位順序。

### 包名稱、升級與相容

- Windows 資料夾：`PromptCalculusStudio`；程式：`PromptCalculusStudio.exe`。
- ComfyUI 擴充資料夾：`comfyui_prompt_calculus_studio`。
- 原始碼包：`PromptCalculusStudio-source.zip`；視窗版本：`v0.82 Alpha 1 Repair 1`。

全新安裝把新擴充資料夾放進 `ComfyUI/custom_nodes`。從舊擴充升級，先關閉 ComfyUI，將原 `comfyui_prompt_studio` 資料夾改名為 `comfyui_prompt_calculus_studio`，再以新版程式檔更新，保留原 `local_library.json` 與個人資料。兩個名稱的資料夾不能同時留在 `custom_nodes`，以免同一擴充載入兩次。重新啟動 ComfyUI 並重新整理網頁後生效。

原預覽入口 `qa/Open-PCS-082.vbs` 更新為新版 EXE，沿用原 `qa/preview-data`；舊程序需由使用者關閉再開啟。直接開啟新版 EXE 使用包旁的 `data`，不會自動找到其他包的資料。

內部 Python 模組、舊節點識別、擴充註冊 ID、`/prompt_studio` API、`PROMPT_STUDIO_DATA`、PNG／快照欄位、ComfyUI 使用者資料路徑保留原識別，避免改名破壞舊工作流及資料。版本格式沒有新增遷移；舊 `generation.chosen` 僅保留相容，0.8.2 的執行不再依賴它。原始碼中的相容模組目錄與歷史文件不做全域取代。

## 驗證結果

離屏檢查涵蓋模型文字可見範圍、切頁、字型與顯示恢復、工作流選擇與取消、占用欄位、讀取失敗／重試、undo／redo／保存、既有 A→B 時序及新包來源。以獨立臨時資料和既有 D 槽依賴驗證。選單離屏畫面已檢視游標移入前後的差異。

原生 Windows 多 DPI／合成器、使用者特定裁切觸發、真實 ComfyUI 自訂工作流／GPU及新機安裝仍待獨立 QA。沒有測試或打包使用者預覽資料、Token 或模型。

本組驗證紀錄：qa/082-repair1-final.log 的 23 項設定／工作流回歸通過，qa/082-picker-compatibility.log 的 21 項選擇器／生成相容／封裝來源檢查通過，qa/082-picker-final.log 的 7 項最終選擇器檢查通過；有重疊用例，不相加計數。150% 專項 2 項、JS 20 項通過。包建置與 EXE 離屏啟動結果另列 build/082_DELIVERY.json。既有連結工作流沿用本地參數；選擇另一個 CLIP 不會重設原參數，需要重讀時使用「重新整理」。

## 收錄與發布

0.8.2 Repair 5 公開日期為 2026-09-27。本次為本機 Repair 1，沒有獨立 GitHub Release。0.8.2 後續公開的是[Alpha 1 Repair 5](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.2-alpha.1.repair.5)；早期候選的跨工作流及背景能力不能直接當成該公開版功能。
