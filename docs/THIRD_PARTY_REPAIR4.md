> 歷史Repair4稽核；0.84實際Windows包核對見 [THIRD_PARTY_084.md](THIRD_PARTY_084.md)。

# Repair 4 依賴與授權附件

本機候選包重用既有 Python、Qt 及建置工具，沒有下載新執行依賴。附件由 06 提供官方固定版本文字，03 核對其 SHA256 後原文採入 docs/third-party；來源網址及雜湊在 NOTICE_SOURCES.json。桌面包另附本機 dist-info 的 METADATA／licenses 及 Python-LICENSE.txt。

| 元件 | 此次既有輸入版本 | 相應資料與限制 |
|---|---|---|
| Python | 3.12.14 | Python-LICENSE.txt；實際 runtime 供應與重建來源未完全確認 |
| PySide6 Essentials／shiboken6／Qt | 6.11.2 | LGPL-3.0-only、GPL-3.0-only 本文及本機套件授權；尚待按 Qt 模組核對內嵌元件、通知及 build options |
| OpenSSL | 3.5.8，依保留基準的 DLL 版本 | OpenSSL-3.5.8-LICENSE.txt；完整通知及 DLL 供應對照待核，沒有用不存在的 NOTICE.txt 代填 |
| SQLite | 3.53.1，依保留基準的 DLL 版本 | 來源供應尚待核對 |
| MSVC runtime | 14.44.35211.0，依保留基準 | 取得來源及適用再散布條件未確認 |
| libffi-8／opengl32sw | 未知 | 檔名不等於精確版本或來源證明 |
| PyInstaller | 6.22.2 | 建置工具；包內附本機 COPYING／METADATA，不把它當全部 runtime 的授權 |
| hooks-contrib／altgraph／pefile／pywin32-ctypes | 2026.7／0.17.5／2024.8.26／0.2.3 | 建置輸入，未因此宣稱全數作 runtime 散布 |

已知來源差異：CPython 3.12.14 上游 get_externals 列 OpenSSL 3.0.16／SQLite 3.49.1.0，與保留基準 DLL 的 3.5.8／3.53.1 不同，不能拿該上游腳本當本機 runtime 的完整來源證明。原 wheel 未保留，PyPI 發行 hash 不等於已核對本機 wheel。完整 Qt 歸屬、相應源碼與替換／重建說明仍待 06 稽核。

專案原始碼授權見根目錄 LICENSE。這份附件補齊可取得的條款本文及已知缺口，不表示公開散布義務已全部完成。本輪仅授權本機隔離候選包，未公開發布。ComfyUI／aiohttp 由宿主提供，擴充包不附整套宿主。
