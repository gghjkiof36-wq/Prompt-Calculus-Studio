# ComfyUI 整合

適用：0.8.6 Alpha 1 · [使用指南](README.md)

先確認本機 ComfyUI 與工作流能正常生成；本版需要保持該工作流網頁開啟。

## 安裝配套擴充

來源主程式內含 `extensions` 與配套安裝腳本，請完整解壓後保留。從 PCS「探索 → ComfyUI → 連線與工作流 → 安裝／更新 PCS 擴充」，或「管理與更新 → PCS 擴充」進入；選 ComfyUI 根目錄、`custom_nodes` 或 Portable 上層，關閉 ComfyUI 後安裝。完成後重啟 ComfyUI 並重新整理網頁，再連線與綁定。

本擴充不新增生成節點，無須用 Manager 辨識；看不到新增節點不表示安裝失敗。管理頁會區分隨附、已安裝及實際載入版本，磁碟更新完成後仍需重啟服務。

「隨 PCS 自動更新配套擴充」跟隨目前主程式隨附版本，ComfyUI 關閉時才安裝；不從網路下載擴充。安裝位置與開關保存在目前資料目錄，新資料需重新指定。原 library 設定保留，舊擴充備份於 `.pcs-program-backups`，不改模型、工作流或圖片。不明來源、降版及無法確認的中斷安裝會停止，處理見[疑難排解](troubleshooting.md)。

## 手動安裝方式

從 [同版 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.6-alpha.1) 取得 `PCS-v0.8.6-Alpha1-ComfyUI.zip`，解壓到 ComfyUI 安裝目錄以外。完整資料夾名稱為 `comfyui_prompt_calculus_studio`；開發原始碼中的同名資料夾缺少打包時加入的共用核心，請使用完整擴充 ZIP。

先啟動一次 PCS 建立資料庫，再關閉 ComfyUI。使用完整擴充包內的安裝腳本，將範例路徑改成實際位置：

```powershell
& 'D:\Downloads\PCS-extension\comfyui_prompt_calculus_studio\install_comfyui.ps1' `
  -Source 'D:\Downloads\PCS-extension\comfyui_prompt_calculus_studio' `
  -ComfyUIRoot 'D:\ComfyUI' `
  -DesktopData 'D:\Apps\PCS\data'
```

已有舊擴充時，確認 ComfyUI 已停止後加 `-Update`。腳本核對完整包與桌面資料庫，將舊擴充保存在 ComfyUI 的 `.pcs-program-backups`；若新舊名稱同時存在或 `DesktopData` 與舊設定不同，會停止而不覆寫設定。先保存並移出不使用的擴充、確認正確資料位置，再重試。不要刪除資料庫或憑證來繞過錯誤。

安裝後重新啟動 ComfyUI 後端並重新整理網頁；舊網頁仍可能使用舊協定。此指南以可管理 `custom_nodes` 的本機安裝為準，官方 ComfyUI Desktop 尚未完成本版相容性驗收。

請直接使用同版擴充附件。開發者自行建包的條件與方法見[建置指南](../development/building.md)。

## 第一次連接與綁定

1. 開啟 ComfyUI 網頁與要使用的工作流。PCS 設定中連接實際本機網址，例如 `http://127.0.0.1:8188`；擴充資料設定指向含 `studio.sqlite3` 的桌面資料夾。
2. 在 PCS 的 CLIP 綁定視窗選擇同一原生工作流與可編輯的文字節點／欄位。不要用同名工作流副本代替原生身分。欄位已被其他 CLIP 綁定時，先解除原綁定。
3. 將 Prompt 控制的文字送入 CLIP 左側入口，包括需要保留的空白稿。CLIP 只套用這個輸入，未綁定的原生欄位保留。
4. 確認文字來源與目標正確，保持該工作流網頁開啟，再將綁定控制接入 Stage，從 PCS 執行。沒有唯一對應網頁、版本不符或綁定失效時，依提示修正後再提交。頂列「已連線」表示服務可達，仍需工作流網頁回應。

文字由上游節點供應時，選可直接編輯的上游來源；不要把所有第三方節點、子圖或欄位視為已支援。

## 生成、圖片流程與預排程

完成 CLIP 綁定後，將文字或圖片輸入的紫色控制線接入 Stage，再執行所選工作流。未接入有效 Stage 時只套用輸入，不生成。

- [Stage 與圖片流程](stage.md)：一次生成、多 Stage 接線、批次結果與下游圖片。
- [Stage 參數](stage-parameters.md)：CFG、步數、尺寸、模型選項、種子與單筆任務設定。
- [預排程與任務管理](scheduling.md)：保存／即時輸入、等待項、暫停、取消及恢復。

## 工作流草稿、種子與恢復

保持所需工作流在原生網頁開啟，綁定重新整理讀取未存檔草稿。跨工作流透過原生切換與提交入口定位，保留各工作流草稿。只讀／切換／套用輸入不額外抽種子；回到相同原生工作流、未變更採樣器時恢復已觀察到的原生生命週期。

若 ComfyUI 已生成圖片但 PCS 沒有顯示，從底部活動任務數開啟該筆紀錄，使用「重取結果／重試失敗項」重新讀取圖片。若工作流切換卡在載入中，先查看 ComfyUI 的佇列與歷史、保存工作流，再重新整理網頁。

生成的圖片未填入下一步的圖片輸入欄位時，可手動選用該圖片；直接再次按執行會開始新的生成。詳細處理方式見[疑難排解](troubleshooting.md)。

## Manager 套件管理

從「探索 → ComfyUI → 管理與更新」查看已安裝套件，切換卡片／清單，按名稱、作者或狀態篩選。選取後可查看版本、來源與詳情；固定版本可排除於批次更新。支援 Manager API v2 的環境可更新單項、所選或全部已啟用套件，確認時可加入 ComfyUI 穩定版。

Legacy Manager 以讀取清單及開啟 ComfyUI 操作為主。若共用更新佇列已有其他工作，先在 ComfyUI 處理；更新結果未確認時按「重新查詢」，不要重複送同一項。顯示「待重啟」後，請自行重啟 ComfyUI 再核對載入。配套 PCS 擴充的安裝／更新走前述專用入口。

## 相容與資料

桌面及擴充使用同一版本；PNG 的 `prompt_studio` 欄位、`PROMPT_STUDIO_DATA` 環境變數與既有資料識別保留相容用途。擴充設定與歷史位於 ComfyUI 的 `user/prompt_studio`，另有指向桌面資料庫的 `local_library.json`。更新時備份擴充及資料；回退使用舊程式、舊擴充與舊資料副本，勿覆蓋新資料。詳見 [資料與隱私](data-backup-privacy.md)。
