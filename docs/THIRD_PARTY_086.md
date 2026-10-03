# 0.8.6 Windows 候選材料核對

核對固定產品與 binary `edad2af00e7ff37051077da470da55590134d1d2`；EXE SHA256 為 `26ab0fc657c2328b1755b7469a9b3043b871acd4f675f403be790da22d9b833c`。

實際包含 59 個 DLL／PYD，其中 58 個與 [既有 runtime 清單](third-party/084/WINDOWS_RUNTIME_INVENTORY.json) 逐檔 SHA256 相同。新增檔案是 `PySide6/QtSvg.pyd`，屬現有 PySide6 Essentials 套件的 SVG 綁定；隨包 PySide6／Shiboken6 metadata 仍為 6.11.2。這些核對不代表兩版 EXE 相同。

第三方附件仍為 Python LICENSE、PySide6 Essentials／Shiboken6／PyInstaller metadata 與部分授權檔。未新增 Qt 各模組完整第三方通知、匹配來源／建置選項、替換與重建步驟，也沒有補齊既有 libffi、MSVC 等供應來源與授權映射。逐項缺口及官方來源保留於 [既有元件稽核](THIRD_PARTY_084.md)。

本版公開提供來源版主程式與 ComfyUI 擴充；上述 Windows 候選不列公開附件。
