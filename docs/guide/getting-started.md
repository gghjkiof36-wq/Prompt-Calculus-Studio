# 開始使用

[回首頁](../../README.md) · 適用：0.8.8 Alpha 1

## 下載與啟動

從[0.8.8 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.8-alpha.1)下載 `PCS-v0.8.8-Alpha1-source.zip`，完整解壓到新資料夾，保留 `run.py`、`Start.cmd`、`extensions` 及安裝腳本。這是來源版，需自行安裝 Windows 64 位元 Python 3.12（含 Python Launcher）。

在含 `run.py` 的資料夾開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py --v0.8.8-alpha
```

首次需網路下載依賴，之後雙擊 `Start.cmd`，或建立它的桌面捷徑。啟動入口使用同資料夾的 `.venv`；找不到 `py` 時，以已安裝 Python 3.12 的完整路徑建立環境。本次沒有 Windows EXE 公開下載；本機桌面包與公開來源版是不同交付。

## 先組合一份文字

1. 從左上工作區入口切到 Prompt Editor，新增文字模塊，保存名稱、文字與分類。需要變化時使用「＋ 新增變體」。
2. 回 Canvas，在畫布內開啟素材挑選頁，加入這份素材的獨立副本。
3. 在 Prompt 輸出確認文字，按「複製 Prompt」。這三步不需要 ComfyUI。

文字模塊、組合、流程分別保存變體、單份文字組合及多組順序。右側「管理」可排序變體、指定起始項；雙擊或「… → 編輯名稱」進入子頁，再用「返回」回到變體清單。詳見[素材與批次](prompt-materials-088.md)。

## 接上 ComfyUI 生成

1. 確認自己的 ComfyUI、模型與工作流原本能正常生成；保存工作流後關閉 ComfyUI。
2. 在 PCS「探索 → ComfyUI → 連線與工作流 → 安裝／更新 PCS 擴充」選擇 ComfyUI 資料夾。也可從「管理與更新 → PCS 擴充」進入，不必先綁定。
3. 完成後由你啟動 ComfyUI，重新整理工作流網頁。回 PCS 連線，在管理頁核對實際載入版本。每次只安裝一份配套；通用與 legacy／modern 是可選相容設定檔，見[相容指南](comfyui-compatibility.md)。
4. CLIP 選擇原生工作流的實際文字節點／欄位；Stage 選擇生成工作流。把 Prompt 輸出接入 CLIP，再將紫色控制線接入 Stage。
5. 確認待設定提示後按「執行」。沒有有效 Stage 時只更新綁定輸入，不生成。生成期間保持 PCS、ComfyUI 後端與工作流網頁開啟。

新工作區有六個起始節點；既有工作區保留草稿、布局與視野。更多接法見[ComfyUI 整合](comfyui.md)與[Stage 參數](stage-parameters.md)。

## 再試一個批次

建立兩個組合，加入流程並設定順序。在 Canvas 新增「讀取流程」，選該流程並接到 Prompt 輸出；CLIP 可直接接 Stage，也可先經預排程保存每筆內容。

畫布文字在每個批次固定，讀取流程按輪供應。十輪配六輪得到十筆，較短來源循環；外層執行三次代表三批。先用少量明確文字核對數量與目標欄位，完整規則見[素材與批次](prompt-materials-088.md)。

## 更新、帶入與回退

更新前備份，保留舊程式、舊擴充與原資料 A，新版使用副本 B；新舊程式不要同時寫同一資料庫。來源版預設資料在 `run.py` 旁的 `data`，可用 `PROMPT_STUDIO_DATA` 另指定位置。

主程式與配套都要更新：0.8.8 支援文件格式 1–7，舊擴充未必能讀新資料。只重啟舊擴充不能解決格式不相容。擴充安裝位置及自動更新開關跟隨目前資料目錄，帶入副本時先核對目標，安裝後由你重啟後端並刷新網頁。

未完成任務重開後先暫停。已保存內容的取值進度不因取消倒退，重試沿原筆值；舊格式有效進度在升級時保留。Stage 沒有參數覆寫時，不會自動補入目前面板值。0.8.3 的等待項需重新安排。

回退時停用擴充自動更新，用相容舊程式、舊擴充及 A；保留 B 的新內容，沒有自動合併保證。特定舊候選 Stage PNG 仍有還原限制，保留原檔，不反覆覆蓋資料。完整備份包含私人素材，生成 PNG 也有必要 Prompt／參照，分享前[檢查內容](data-backup-privacy.md)。`SHA256SUMS.txt` 用於完整性核對，並非數位簽章。
