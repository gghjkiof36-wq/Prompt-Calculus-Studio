# 0.8.8 第三方元件與散布材料

[版本主頁](README.md) · [來源與重建](source-provenance.md) · [專案授權](../../guide/licensing.md)

## 本版下載

PCS 專案授權為 **AGPL-3.0-only**。本版公開來源 ZIP、通用及 legacy／modern ComfyUI 擴充與校驗檔；這些附件不含 Python、Qt 或其他 Windows runtime 二進位檔。來源版需自行安裝 Python 與相依套件。

## Windows 成品的實際元件

Windows 成品從 `c3718fd7b12ac9111a13c3fe8d6bba82cf7e0081` 全新編譯。[逐檔清單](windows-runtime-files.json)記錄 1 個 EXE、59 個 DLL／PYD 的檔名、大小、版本與 SHA-256。

| 元件 | 實際版本與判定依據 | 條款與材料入口 |
|---|---|---|
| CPython | 3.12.14；Python DLL／PYD 版本及建置直譯器 | [Python 3.12 授權與內含軟體聲明](https://docs.python.org/3.12/license.html)；隨包已有 `Python-LICENSE.txt` |
| PySide6 Essentials／shiboken6 | 6.11.2；安裝套件 METADATA，相關 DLL／PYD 已逐檔列入清單 | METADATA 宣告 `LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only`；[官方授權說明](https://doc.qt.io/qtforpython-6/licenses.html)、[6.11.2 來源](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/) |
| Qt Core／Gui／Network／Svg／Test／Widgets 及外掛 | 6.11.2.0；DLL 版本 | [Qt 授權](https://doc.qt.io/qt-6/licensing.html)、[開源散布義務](https://www.qt.io/development/open-source-lgpl-obligations)、[6.11.2 來源](https://download.qt.io/archive/qt/6.11/6.11.2/single/) |
| OpenSSL | 3.5.8；`libssl`／`libcrypto` 版本及建置直譯器 | [官方授權](https://openssl-library.org/source/license/)：3.x 採 Apache-2.0；完整通知及實際來源對照仍需補入 Windows 散布材料 |
| SQLite | 3.53.1；DLL 與建置直譯器回報相同 | [官方 public domain 說明](https://sqlite.org/copyright.html) |
| Microsoft Visual C++ runtime | 14.44.35211.0；MSVCP／VCRUNTIME 五個 DLL 的版本 | 隨包 Python 聲明包含 Microsoft Distributable Code；仍須核對這批二進位檔的來源與[可散布條件](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170) |
| libffi | `libffi-8.dll` 沒有可讀版本資源，精確版本未確認 | [上游條款](https://github.com/libffi/libffi/blob/master/LICENSE)；尚缺此 DLL 的版本、來源及通知對照 |
| 軟體 OpenGL | `opengl32sw.dll` 含 `Mesa 11.2.2` 字串；完整內含元件與建置配置未確認 | [Mesa 條款](https://docs.mesa3d.org/license.html)；需按實際內含元件補齊來源與通知 |
| Python 內含輔助庫 | libmpdec 2.5.1、zlib 1.3.2、Unicode 資料 15.0.0；bzip2 含 1.0.8 字串；liblzma 精確版本未確認 | 依 [Python 內含軟體條款](https://docs.python.org/3.12/license.html#licenses-and-acknowledgements-for-incorporated-software)逐項核對；Python 隨包聲明已含 bzip2，其他實際通知與來源對照尚未閉合 |
| PyInstaller bootloader／runtime hooks | 6.22.2；建置套件與實際 EXE | [該版 COPYING](https://raw.githubusercontent.com/pyinstaller/pyinstaller/v6.22.2/COPYING.txt)：bootloader 有 GPL 例外，runtime hooks 採 Apache-2.0；該份 COPYING 已隨包附上 |

建置工具另使用 pyinstaller-hooks-contrib 2026.7、altgraph 0.17.5、packaging 26.3、pefile 2024.8.26、pywin32-ctypes 0.2.3。它們是建置環境套件，不能直接當作全部隨包 runtime 的清單。

## Windows 下載尚缺的材料

目前 PySide6／shiboken 的隨包 `licenses/` 只有 `LicenseRef-Qt-Commercial.txt`，缺完整開源授權條款與對應通知。另須補齊：

- Qt、圖片外掛與軟體 OpenGL 內含第三方元件的精確版本、著作權與通知對照。
- 對應實際 Qt／PySide／shiboken 二進位檔的來源、建置配置及重建／替換方法；上游同版來源連結尚不足以確認這批成品的完整對應材料。
- libffi／liblzma 的精確來源，以及其餘上述元件缺少的通知與散布依據。

因此本版不提供 Windows EXE 下載；請使用來源版安裝。重新散布自行打包的 Windows 成品前，需按實際納入元件補齊這些材料。
