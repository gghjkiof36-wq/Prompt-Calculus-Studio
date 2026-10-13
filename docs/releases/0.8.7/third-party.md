# 0.8.7 Windows 候選：第三方材料核對

固定包來源：Candidate28 `c66c7b4c73b6ace4e649c6f6cd4379f0fd1b79b1`，核對日期2026-10-13。這份紀錄適用本機Windows候選，來源版與ComfyUI附件不包含下列第三方runtime。

## 實際元件

| 元件 | 識別依據與版本 | 條款／材料狀態 |
|---|---|---|
| CPython | `python312.dll`：3.12.14 | 已有Python LICENSE；內嵌標準庫相依的版本／聲明仍需逐项對照。 |
| PySide6 Essentials、Shiboken6、Qt | dist-info：6.11.2；Qt Core/Gui/Widgets/Svg DLL：6.11.2.0 | 隨包只有metadata及Qt商業條款檔，不能據此認定開源散布材料完整。 |
| OpenSSL | libcrypto/libssl DLL：3.5.8 | docs有Apache-2.0授權原件；仍需與實際build/source逐項對照。 |
| SQLite | sqlite3.dll：3.53.1.0 | 版本從實際DLL取得；對應來源／納入方式未由此版本資料單獨建立。 |
| libffi | libffi-8.dll，無FileVersion | 不以DLL尾碼猜版本；尚缺精確build及來源對照。 |
| Microsoft Visual C++ runtime | MSVCP/VCRUNTIME DLL：14.44.35211.0 | 尚缺實際供應来源、散布條款與通知映射。 |
| PyInstaller | builder metadata：6.22.2 | 已有COPYING及bootloader例外metadata；建置材料仍需固定。 |

已逐檔記錄59個DLL/PYD的路徑、版本、大小與Hash；Qt內嵌的第三方程式仍需對實際模組清單核對，不能只由DLL數量判斷完成。

## 未過的Windows門檻

- Qt/PySide6/Shiboken匹配來源、建置選項及修改狀態，及可用的來源提供方式。
- 實際使用Qt模組及內嵌第三方的完整授權、通知與聲明。
- 替換Qt庫、重建及執行替換後程式的說明與驗證。
- libffi、Microsoft runtime和CPython內嵌相依的精確版本、來源及條款映射。

[Qt官方說明](https://www.qt.io/development/open-source-lgpl-obligations)列出相應來源、庫替換與通知等義務；[Qt for Python通知](https://doc.qt.io/qtforpython-6/licenses.html)指出仍需核對实际用到的第三方元件。此處記錄已備妥材料與缺口，不替代對條款適用性的法律確認。

## 公開下載範圍

0.8.7提供PCS來源主程式、同版ComfyUI擴充及SHA256校驗。主程式需自行安裝Python及requirements；本機Windows EXE候選保留，不列本次公開附件。來源及包識別見[來源對照](source-provenance.md)。
