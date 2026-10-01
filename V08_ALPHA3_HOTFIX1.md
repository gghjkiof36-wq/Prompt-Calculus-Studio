# v0.8.0 Alpha 3 修正包 1

修正選擇工作流或綁定 Prompt 後，額外出現只有「Denoise 1」的小視窗。

原本的提示元件未放入介面容器，更新參數時卻被顯示成獨立視窗。現在移除這個元件，摘要改為「參數…」按鈕的滑鼠提示；Denoise 仍可在參數視窗調整。

本包位於獨立資料夾，使用從 Alpha 3 複製的資料。請先關閉 Alpha 3，再開啟本包的 `Start-v0.8-Alpha.vbs`。原程式和資料均保留，修正前另有完整備份。

## ComfyUI 擴充

隨包附上 `PromptStudio-v0.8-ComfyUI.zip`，與已提供的 Alpha 3 擴充相同，包含多文字綁定、直接執行、工作流匯出到桌面與第 4 版快照相容性。

1. 停止 ComfyUI，將原 `custom_nodes/comfyui_prompt_studio` 備份至 `custom_nodes` 以外的位置，避免同時載入兩份擴充。
2. 將 ZIP 中的 `comfyui_prompt_studio` 資料夾放入 `custom_nodes`。
3. 重新啟動 ComfyUI，並重新整理網頁。若網頁仍保留舊介面，使用 Ctrl+F5 強制重新整理。
4. 在桌面設定中重新連線，選擇工作流，綁定各 Prompt 輸出的文字欄，再回到工作區執行。

此次修正不改動生成參數或提交內容。相關工作流與生成測試、原生 Windows 視窗驗證位於 `驗收紀錄`；本輪未重新執行模型生圖品質驗收。
