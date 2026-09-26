# Repair5 發布來源對照

本次代表版本為 `v0.82-alpha.1-repair.5`。產品固定來源為 `5c621ecfdd4b30f8ade31dd6446dc9830871259a`，包含 2026-09-27 綁定修復與整批圖片預覽修復。

公開提交由原公開 main `048a3ac0a49e698db5ec85b80b31a9964e67fd10` 衍生，保留既有公開歷史與 0.81 tag。這是已核對的固定產品樹整合，不是全部本機開發提交的合併；上述本機來源SHA可能不在公開Git物件中。可下載內容應以本Release tag、附件BUILD_INFO與manifest核對。

- `prompt_studio/`、`comfyui_prompt_studio/`、`tests/`及根目錄執行／建置／部署腳本對齊上述固定來源，不加入工作區未提交WIP。
- 公開使用文件由07依目前行為更新；既有公開圖片與研究文件保留。歷史技術文件另標適用時點，機器絕對路徑替換成工作區佔位，不將過期驗收描述當本版結果。
- 來源ZIP依既有`package_documents.source_paths`白名單收集，包含執行、建置、測試與文件；不含Git歷史、私人資料、模型、第三方runtime、vendor或研究素材。排除的歷史README截圖僅在Git倉庫可查，來源ZIP的啟動不依賴它。
- ComfyUI ZIP含固定擴充、SHARED_MODULES核心、文件及相同來源ZIP。`PACKAGE_MANIFEST.json`描述實際交付檔，`SOURCE_MANIFEST.json`描述專案來源；`BUILD_INFO.json`指出發布提交及內嵌來源ZIP的SHA256。兩個manifest用途不同。
- 程式內版本字串仍帶「0927-2 批次預覽候選」，為固定已人工校驗來源的原字串；公開版本保持Alpha prerelease，不代表全部Repair5需求完成。

本輪按使用者指示沿用人工校驗通過的成果，只核對Git、來源／包一致性、敏感檔排除及GitHub上傳完整性，未重跑產品、GPU、公開下載安裝或CI測試。

目前需開啟ComfyUI網頁，同一工作流每次執行一次。跨工作流接續、背景無網頁執行及多圖下游依序逐張仍未完成。未提供Windows EXE：既有Qt／PySide6等完整散布附件、相應runtime來源與重建／替換證據尚未齊備，本次沒有重新散布第三方執行環境。
