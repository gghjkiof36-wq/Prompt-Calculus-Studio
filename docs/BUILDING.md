# 建置與版本（2026-09-14）

`prompt_studio/releases.py` 集中版本名称、輸出目錄、啟動器與參數；`SHARED_MODULES` 集中 ComfyUI 純 Python 共用模組清單。`build_windows.py --v081-maintenance` 只輸出到 `build/v081-maintenance-1/PromptStudio`，採低優先級。必要依賴沿用原 PySide6 與 PyInstaller，無新第三方依賴。

`build_windows.py` 先在新的 staging 目錄收集執行檔，再合併程式檔，拒絕重新導向／超出工作目錄的目的地；不刪除使用者的 portable data。帶版本旗標都不覆寫 `release`。原有一般 `--stage-only`、diagnostic 和歷史版本旗標保留。

桌面版本標題讀取打包時的 `assets/build-info.json`；來源執行使用 CURRENT。`package_documents.bundle_documents` 附逐檔 SHA256、來源 ZIP Hash、Git 提交、未提交清單、建置時間和驗證文件位置。若無 Git 可讀取，以來源清單為準，不杜撰提交。文件補記後，`binary_git_head` 保留實際 EXE 編譯來源提交。

配套擴充由 `build_comfyui.py --v08 --v081-maintenance` 產生；保留 `comfyui_prompt_studio` 根目錄名稱。測試解壓後禁止 Qt 匯入，實際匯入共用模組並恢復舊快照，防止新增依賴漏包。

程式與來源 ZIP 都排除 data、qa、vendor 與 Git 內部資料；測試副本只在 qa。這不是公開發布指令，發布和外部服務驗證另行處理。
