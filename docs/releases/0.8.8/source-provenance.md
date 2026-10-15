# 0.8.8 Alpha 1 來源與建置

[版本主頁](README.md) · [建置指南](../../development/building.md) · [第三方元件](third-party.md)

## 來源識別

本版包含已複驗的功能來源 `0d4a5f5a9ef51a98137a18522c0d2bf110ca3b61`，並整合正式版本識別、建置路徑及公開文件。完整開發提交保留在 Git 歷史。

公開來源以 [v0.8.8-alpha.1 標籤](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/tree/v0.8.8-alpha.1)為準。包內 `BUILD_INFO.json` 的 `git_head` 記錄該成品實際來源提交；桌面成品另以 `binary_git_head` 記錄 EXE 的編譯提交。功能複驗來源與發布整合提交分開記錄。

## 下載項目

[0.8.8 Alpha 1 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.8-alpha.1)

| 檔名 | 內容 |
|---|---|
| `PCS-v0.8.8-Alpha1-source.zip` | 可建立 Python 環境啟動的 PCS 來源、文件與同版配套擴充 |
| `PCS-v0.8.8-Alpha1-ComfyUI.zip` | 通用 ComfyUI 擴充，包含兩種相容設定檔 |
| `PCS-v0.8.8-Alpha1-ComfyUI-legacy-0.39.2.zip` | legacy 相容設定檔：backend 0.39.2／frontend 1.53.10 |
| `PCS-v0.8.8-Alpha1-ComfyUI-modern-0.40.0.zip` | modern 相容設定檔：backend 0.40.0／frontend 1.55.17 |
| `SHA256SUMS.txt` | 上列 ZIP 的 SHA-256 校驗 |

相容設定檔用來標明套件配置；本版仍需搭配 ComfyUI 後端與工作流網頁。操作與版本限制見[相容指南](../../guide/comfyui-compatibility.md)。

## 包內對照

- `SOURCE_MANIFEST.json`：專案來源逐檔大小與 SHA-256。
- `PromptCalculusStudio-source.zip`：對應專案來源快照，包含建置腳本；沒有 Git 歷史。
- `PACKAGE_MANIFEST.json`：實際交付檔案的大小與 SHA-256。
- `FUNCTIONAL_SOURCE_MAP.json`：已受驗功能來源與發布來源的檔案對照，另列版本識別調整。
- `RUNTIME_SOURCE_MAP.json`：擴充程式與 33 個共享模組的來源對照。
- `COMFYUI_COMPATIBILITY.json`：擴充相容設定檔及可讀文件格式版本。

生成的 `shared/` 來自 `prompt_calculus_studio/releases.py` 的 `SHARED_MODULES`；三種擴充使用相同共享核心，來源不含 Qt 匯入。

## 重建

建置環境為 Windows 64 位元、Python 3.12.14、PySide6 Essentials／shiboken6 6.11.2、PyInstaller 6.22.2。需使用實際 Git checkout：

```powershell
git clone https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio.git
cd Prompt-Calculus-Studio
git checkout v0.8.8-alpha.1
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install PySide6-Essentials==6.11.2 shiboken6==6.11.2 PyInstaller==6.22.2 pyinstaller-hooks-contrib==2026.7 altgraph==0.17.5 packaging==26.3 pefile==2024.8.26 pywin32-ctypes==0.2.3
.\.venv\Scripts\python.exe build_windows.py --v0.8.8-alpha.1
```

輸出位置與單獨建置擴充的方法見[建置指南](../../development/building.md)。這些指令重建 PCS 成品；第三方庫自身的來源與建置材料須另依其條款準備，見[第三方元件](third-party.md)。
