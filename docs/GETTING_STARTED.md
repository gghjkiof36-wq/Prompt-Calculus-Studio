# 開始使用

[回首頁](../README.md)

## 準備環境

目前以 Windows 64 位元測試。單純編輯 Prompt 與管理圖片不需要安裝 ComfyUI；要直接生成圖片，請先確認你的 ComfyUI 工作流能正常執行。

## 取得並啟動原始碼

1. 在 [專案首頁](https://github.com/gghjkiof36-wq/modular-prompt-manager) 選 **Code → Download ZIP**，解壓到可寫入的資料夾。
2. 安裝 Python 3.12（64 位元），包含 Python Launcher。
3. 在包含 `run.py` 的資料夾開啟 PowerShell，依序執行：

```powershell
py -3.12 setup_dependencies.py
py -3.12 run.py
```

安裝指令會將 PySide6 Essentials 與 shiboken6 下載到專案的 `vendor` 目錄；需要網路。之後可重複執行第二個指令啟動。

如果 `py` 無法辨識，確認 Python Launcher 已安裝；也可使用 Python 3.12 執行檔的完整路徑。遇到啟動錯誤時，保留終端機中的訊息供排查。

原始碼的開發進度可能比已發布版本新。需要特定版本時，請使用該 Release 或 Git tag 對應的原始碼。

## 如果已有 Windows 封裝包

前往 [Releases](https://github.com/gghjkiof36-wq/modular-prompt-manager/releases)，查看是否提供 Windows 程式附件。沒有附件時，請使用原始碼方式。

下載封裝包後，完整解壓並開啟 `PromptStudio.exe`。保留旁邊的 `_internal` 資料夾；封裝版不需要自行安裝 Python。GitHub 自動提供的「Source code」附件只有原始碼。

## 建立第一份 Prompt

1. 左側選擇模組，或按「＋」建立自己的分類。
2. 在素材區按「新增」，填入容易辨識的名稱與實際 Prompt。
3. 點選素材，右側會顯示目前組合與最終文字。
4. 按「複製完整 Prompt」。

你可以拖曳右側群組調整輸出順序，或用素材的 −／＋調整權重。雙欄之間的分隔線可以拖動，依閱讀需要分配寬度。

直接修改最終文字會切換成手動版本。按「清除內容」可清除手動稿並回到原本組合；已選素材會保留。

## 連接 ComfyUI

依 [ComfyUI 操作指南](../COMFYUI_GUIDE.md) 建立擴充包並安裝。0.7.1 需在 ComfyUI 網頁指定文字欄，啟用桌面控制，再從 Prompt Studio 連接。

## 資料保存位置

- 原始碼版：`run.py` 所在資料夾的 `data`。
- 封裝版：`PromptStudio.exe` 旁的 `data`。
- 若自行設定 `PROMPT_STUDIO_DATA`，則使用該位置。

兩種啟動方式可能使用不同資料夾。找不到原本的素材時，先確認開啟的是哪一份程式，避免直接匯入覆蓋資料。

## 更新與移機

更新前，先由「資料與備份」建立 ZIP 備份，再關閉程式。將新版本放到另一個資料夾，依 [資料與備份說明](DATA_AND_PRIVACY.md) 複製或還原資料，確認正常後再整理舊版本。

搬移封裝版時可複製整個程式資料夾。外部連結的圖片與 ComfyUI 模型需要另外搬移，或在程式內重新指定位置。
