# 0.831 Alpha 1 來源與建置

本次公開source／ComfyUI擴充，不附Windows執行環境；本機Windows候選的第三方核對與缺口見[THIRD_PARTY_0831.md](THIRD_PARTY_0831.md)。固定產品與既有EXE編譯來源ea7df89；發布文件整合提交另記，不冒用作binary_git_head。

`prompt_studio/releases.py` CURRENT為 `--v0831-repair3`，啟動參數仍為 `--v083-alpha`，版本字串保留「v0.831 種子與流程恢復修復候選（0930）」。22項SHARED_MODULES由同一固定產品來源收集。一般來源啟動依[入門指南](GETTING_STARTED.md)；Git checkout可執行標準建置腳本，解壓source ZIP不含Git歷史，不放寬Git來源白名單來包含本機檔案。

公开包使用Git blobs建立，BUILD_INFO指出公開與產品來源，SOURCE_MANIFEST描述源檔、PACKAGE_MANIFEST描述交付檔；擴充内嵌同一source ZIP和來源映射。Windows固定候選以 `build_windows.py --v0831-repair3` 建立，原包與binary hash见散布核對。公開Windows須另外補實際runtime來源／通知與Qt重建替換材料並核對包；本次沒有重新建置EXE或正式替換安裝。

---

以下保留舊版建置紀錄，旗標及驗證各依原時點。

# 0.83 Alpha 1 來源與建置

本Release只附專案來源與ComfyUI擴充，不附EXE、Python、Qt或PySide6執行環境。固定產品來源與公開提交的關係見 [SOURCE_PROVENANCE.md](SOURCE_PROVENANCE.md)；一般使用者依[入門指南](GETTING_STARTED.md)啟動來源。

`prompt_studio/releases.py` 的CURRENT為 `--v083-direct`，程式參數為 `--v083-alpha`，內部版本保留 `v0.83 直接執行修復候選（0928）`。`build_comfyui.py` 使用CURRENT和17項SHARED_MODULES；Git checkout下的來源白名單由 `package_documents.source_paths` 管理，解壓來源本身不含Git歷史。標準builder可由Release tag的Git checkout執行，來源必須已登記，不應放寬白名單以包含本機私有檔。

公開附件含來源清單、實際套件清單及SHA256；發布組以固定Git blobs建立公開包，加入公開提交／產品提交與內嵌來源映射，未執行Windows建置。重建EXE與正式散布需另完成實際runtime授權、來源與替換材料，以及乾淨安裝／升級／回退驗收。本次不宣稱這些已通過。

---

以下保留0.81建置紀錄，僅適用舊旗標及舊版本。

# 建置與版本（2026-09-14）

`prompt_studio/releases.py` 集中版本名称、輸出目錄、啟動器與參數；`SHARED_MODULES` 集中 ComfyUI 純 Python 共用模組清單。`build_windows.py --v081-maintenance` 只輸出到 `build/v081-maintenance-1/PromptStudio`，採低優先級。必要依賴沿用原 PySide6 與 PyInstaller，無新第三方依賴。

`build_windows.py` 先在新的 staging 目錄收集執行檔，再合併程式檔，拒絕重新導向／超出工作目錄的目的地；不刪除使用者的 portable data。帶版本旗標都不覆寫 `release`。原有一般 `--stage-only`、diagnostic 和歷史版本旗標保留。

桌面版本標題讀取打包時的 `assets/build-info.json`；來源執行使用 CURRENT。`package_documents.bundle_documents` 附逐檔 SHA256、來源 ZIP Hash、Git 提交、未提交清單、建置時間和驗證文件位置。若無 Git 可讀取，以來源清單為準，不杜撰提交。文件補記後，`binary_git_head` 保留實際 EXE 編譯來源提交。

配套擴充由 `build_comfyui.py --v08 --v081-maintenance` 產生；保留 `comfyui_prompt_studio` 根目錄名稱。測試解壓後禁止 Qt 匯入，實際匯入共用模組並恢復舊快照，防止新增依賴漏包。

程式與來源 ZIP 都排除 data、qa、vendor 與 Git 內部資料；測試副本只在 qa。這不是公開發布指令，發布和外部服務驗證另行處理。
