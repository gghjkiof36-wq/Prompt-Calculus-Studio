# 0.8.4 Windows runtime 散布核對（2026-09-30）

本頁沿用改名前候選的實際稽核證據，沒有以0.8.4名稱重建該EXE。核對對象是固定产品／binary `ea7df89dc714fc83c7e11ca32e5c7655619ec8a0` 的乾淨Windows候選。原ZIP SHA256 `f69a954e95d35092d2d41c6b3b555a6883867c9b72a5fe879361cadecfb541c4`，EXE SHA256 `b09819f062100240e2012c9f89c472ece405ffd119b429bdfe264a991a401b23`。274項套件清單逐檔雜湊核對通過；原包保留，不作公開附件。本次公開交付為source／ComfyUI，沒有重新散布下列runtime。

[逐檔清單](../../third-party/084/WINDOWS_RUNTIME_INVENTORY.json)列58個DLL／PYD的大小、SHA256、可取得的PE版本及來源尚未確認狀態；另記Python base_library.zip雜湊和96個Qt翻譯檔。檔案版本僅是二進位資源宣告，不能證明原wheel、編譯選項或完整來源。

| 實際包內容 | 已核實 | 尚需補齊 |
|---|---|---|
| CPython：14個DLL／PYD、base_library.zip | PE版本3.12.14，附Python-LICENSE；取得固定v3.12.14官方LICENSE | 供應套件／編譯輸入、stdlib及靜態bzip2／xz／zlib等實際版本和通知映射 |
| Qt／PySide6：33個DLL／PYD及96個翻譯檔 | Qt DLL 6.11.2.0，PySide6 METADATA 6.11.2；含Core、Gui、Network、Svg、Test、Widgets、平台／圖像／網路plugins與opengl32sw | 原wheel及來源hash、Qt模組實際第三方SBOM／通知、完整相應源碼、build options與重建／替換說明；opengl32sw未提供版本資源 |
| shiboken6：2個DLL／PYD | METADATA 6.11.2；附件仅LicenseRef-Qt-Commercial | 官方開源條款與權利映射、固定來源與build inputs；商業附件本身不代表持有商業授權 |
| OpenSSL：libcrypto、libssl | PE 3.5.8，固定版本Apache-2.0 LICENSE已核官方hash | DLL供應／patch與原版對照，實際適用copyright與NOTICE等來源附件核對 |
| SQLite：sqlite3.dll | PE 3.53.1.0；官方SQLite核心為public domain | 實際DLL供應／來源／編譯選項，不能僅依檔名或上游一般聲明推定該二進位 |
| libffi-8.dll | 檔案hash已固定，無PE版本資源 | 精確版本、供應者、MIT等適用條款、copyright與來源映射 |
| MSVC：5個DLL | PE 14.44.35211.0 | 原取得方式、適用Microsoft再散布授權與REDIST清單對照，不能以檔名推定權利 |
| PyInstaller bootloader及封裝標準库 | 保留建置工具METADATA 6.22.2和COPYING；固定官方COPYING已取得 | bootloader來源／建置選項與本EXE對照；hooks及建置輸入版本另保留，不能全當runtime |

**Windows公開gate：未通過。** 公開來源與擴充的包核對不能抵銷runtime散布缺口，也不把本機離屏啟動視為新機／GPU通過。內嵌歷史測試驅動器還有本機絕對路徑，公開包亦需改用可攜路徑和相符manifest後另驗。沒有使用者資料庫或私人圖片不等於散布材料已齊備。

## 官方來源與可取得材料

- [Qt 6.11.2第三方清單](https://doc.qt.io/qt-6/licenses-used-in-qt.html)按模組列Qt所使用的第三方程式；實際啟用項目仍須對應本wheel/build配置，不能把整張網頁當成已完成的隨包通知。
- [Qt開源義務說明](https://www.qt.io/development/open-source-lgpl-obligations)要求相應來源、使用者可替換／重新連結及相關通知，並指向完整條款；本文件不提供未確認的法律合規結論。
- [Qt for Python條款](https://doc.qt.io/qtforpython-6/licenses.html)是查核入口；實際包METADATA聲明LGPL/GPL選項，附件卻只有商業文字和另存的GNU本文，仍須補完整元件歸屬。
- [CPython v3.12.14 get_externals](https://github.com/python/cpython/blob/v3.12.14/PCbuild/get_externals.bat)列OpenSSL3.0.16和SQLite3.49.1.0，與本包3.5.8／3.53.1不同；已取得官方腳本留核對證據，不能稱它是本runtime的完整重建輸入。
- [SQLite官方聲明](https://sqlite.org/copyright.html)、[Microsoft再散布文件](https://learn.microsoft.com/en-us/cpp/windows/redistributing-visual-cpp-files?view=msvc-170)僅作權利來源查核入口，尚未對本包具體供應履行完成。
- 固定版本CPython／OpenSSL／PyInstaller條款原文已下載並附於[通知來源清單](../../third-party/084/NOTICE_SOURCES.json)，每份記官方URL、取得結果與SHA256。取得條款不代表所有相應來源／通知已齊；未用不存在的OpenSSL NOTICE.txt代填。

## 重建與替換資料仍需完成

從Release tag的Git checkout可取得專案建置腳本；source ZIP不附Git歷史與第三方runtime。新建置入口為 `build_windows.py --v0.8.4-alpha.1`；下列原候選的EXE編譯來源仍固定ea7df89，後續只補文件也不得改稱binary由公開文件提交重建。

尚須固定Python供應／wheel檔與hash、22個專案共享模組、PyInstaller／hook輸入、Qt各實際模組及其third-party build flags；取得匹配來源或有效的來源提供方式，補使用者替換相容DLL／PySide6並重建PCS的具體步驟與驗證。未執行這些重建替換驗證，也沒有授予商業替代授權或CLA。
