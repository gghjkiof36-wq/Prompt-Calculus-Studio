# 0.8.5 Windows 候選材料核對

核對固定產品與 binary 提交 `3033acc9a9f69481dbd2a33565d4fad08f12e3f6` 的 Windows 候選。ZIP SHA256 為 `370dc9b91cbce05a2a47b95b63d7c3b87a4103c8a29d6d7133e788914634f1eb`，EXE SHA256 為 `36c1d028da80ecd77eecf582dcced9b5c653bbc23385ea0189b4638ae4ea93b6`。

實際 58 個 DLL／PYD 與 [0.8.4 runtime 清單](../../third-party/084/WINDOWS_RUNTIME_INVENTORY.json) 逐檔 SHA256 相同，因此其元件辨識與尚未建立的來源對照仍適用；這不表示兩版 EXE 相同。

隨包第三方資料為 Python LICENSE、PySide6 Essentials／Shiboken6／PyInstaller 的 metadata 和部分授權檔。包內仍未提供 Qt 各模組實際第三方通知與匹配的完整來源／建置選項、DLL 替換與重建步驟；libffi、MSVC 等供應來源及授權映射亦未補入。逐項原因與官方來源見 [既有元件稽核](../0.8.4/third-party.md)。

這次公開下載提供專案原始碼與 ComfyUI 擴充；該 Windows 候選保留作原交付證據，不列入公開附件。
