# 0.8.6 Alpha 1 來源對照

公開歷史接續 `9c1b15a2e81fd2c0b3129f8e4bafa92cd8d034dc`，固定功能來源為 `a8e37b40e5abb57642975e9c1e64a3da7fe6a21f`。整輪功能差異涵蓋 0.8.5 產品 `3033acc9a9f69481dbd2a33565d4fad08f12e3f6` 至該提交，包含視覺、Canvas、連線、排程、擴充管理與後續修正。

- 產品程式依固定来源整合；公開適配為 `releases.py` 增加 0.8.6 Alpha 1、`Start.cmd` 使用對應啟動參數，以及来源白名單納入已提交的 PNG 圖片。歷史旗標及資料／節點識別保留。
- 24 個共享模組與擴充來自同一固定公開來源；`RUNTIME_SOURCE_MAP.json` 映射到原檔，`FUNCTIONAL_SOURCE_MAP.json` 保留產品與公開程式的逐檔對照。
- `BUILD_INFO.json` 記公開提交、固定功能來源與版本；`SOURCE_MANIFEST.json` 描述 Git 來源，`PACKAGE_MANIFEST.json` 描述各層實際交付檔。
- 公開來源版下載包包含可離線安裝的同版擴充資料夾與 ZIP，配套 ZIP 與獨立 ComfyUI 下載逐位元相同。擴充內嵌的 `PromptCalculusStudio-source.zip` 為純 Git 來源快照；它不再內含已建置擴充，避免遞迴封裝。內嵌快照與來源版下載包的用途、雜湊分別標記。
- 個人資料、憑證、模型、Git 內部目錄及第三方執行環境均不納入來源或擴充下載。

Windows 內部候選的 binary 保留原 `a8e37b40` 提交與雜湊，不改名為公開整合提交的重新建置成果。材料核對見 [THIRD_PARTY_086](THIRD_PARTY_086.md)。
