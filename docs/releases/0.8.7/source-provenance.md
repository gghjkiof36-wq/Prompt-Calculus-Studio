# 0.8.7 Alpha 1 來源對照

固定功能來源為 Candidate28 後的排序歸屬修復 `62aabb4a4043dab206571d0005c5ae0166435363`，延續0.8.6及0.8.7的實際開發歷史。公開整合以 `76e814d4656b05bfd9f50398aec8226744ea67f1` 為第一父系，合併共同基準 `7f8d3ff` 之後的產品分支提交，保留原提交而不重建為單一快照。

來源包含測試前提修訂 `5cc80658968154e3422a9811ba5b78f0365f7dd3`，此提交沒有改產品功能；功能比對仍固定於62aabb4。

## 程式與版本身份

主程式与擴充的功能檔按固定來源逐檔核對。公開適配僅新增0.8.7 Alpha1版號、啟動旗標／CURRENT、驗證文件引用，以及面向使用者文件。`FUNCTIONAL_SOURCE_MAP.json` 比對固定產品與公開來源，明列換行標準化及版本身份差異；不把舊候選EXE標示為公開提交重建。

## 來源包與擴充

`SOURCE_MANIFEST.json` 與 `PromptCalculusStudio-source.zip` 是純Git追蹤來源快照。主程式下載版另外包含 `extensions/comfyui_prompt_calculus_studio` 及同版擴充ZIP，安裝腳本使用這份配套；來源內嵌的純快照不再包含配套產物，避免遞迴封裝。

`RUNTIME_SOURCE_MAP.json` 將擴充原檔與27個shared模組逐檔映射回來源；其Hash以實際入包檔核對。`PACKAGE_MANIFEST.json` 描述交付層，`BUILD_INFO.json` 記錄實際公開來源提交與驗證文件。下載頁SHA256校驗核對外層附件。

## 圖像與執行環境

Canvas及Prompt Editor圖片來自同版實際Qt程式，以獨立合成資料離屏擷取；有安排範例卡片位置與大小，沒有使用個人素材或真服務結果。圖像展示介面，不代表ComfyUI／GPU生成驗證。

## Windows候選

本機Candidate28 EXE的binary來源仍為`c66c7b4c`。Windows第三方材料尚缺的具體項目見[元件核對](third-party.md)，不列公開附件。來源與擴充不含Python、Qt、PySide6或Microsoft runtime。
