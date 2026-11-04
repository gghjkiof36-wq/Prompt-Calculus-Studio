# Prompt Calculus Studio 0.8.10 Pre-alpha2

在本機整理文件、Skill 與專案，為不同工作設定 Agent，再從聊天交代查找與整理任務。這版也改善聊天閱讀、串流，以及反覆開關 Prompt／Canvas 的反應。

[開始使用](../../guide/workspace-0810.md) · [工作資源與 Agent](../../guide/work-resources.md) · [本機模型](../../guide/local-ai.md)

## 工作資源：文件、Skill 與專案

從「探索 → 工作資源」進入，左側切換檔案、Skill 與專案。加入資料夾後可搜尋、閱讀及整理工作區文件，也可從 ChatGPT ZIP／JSON、Codex JSONL 或 PCS 對話挑選內容，逐份輸出 Markdown；原始封存與可取得附件一併保留。

Skill 可加入完整資料夾，查看簡介、版本、正文與附檔。想修改時，建立管理副本再編輯。專案保存共用規則與參考來源，專案內的 Agent 各自選擇模型、Skill 和角色規則。

## 在聊天使用 Agent

在聊天的「⋯ → 協作設定」選擇角色。可直接交代工作讓模型選技能，或從「指定 Skill」加入 `@名稱`。Agent 使用已設定的來源搜尋、讀檔並回答；回覆旁可查看實際讀取紀錄。工具讀取可選每次詢問或自動審查，停止後保留已取得的內容。

需要從外部交代工作時，先在專案啟用「本機委派」，再使用來源包的 `tools/pcs_agent.py` 選實例、專案和 Agent，提交任務、查進度、取回草稿或停止。成果保存為 `.pcs-chat`，同一任務可在重開後查回。[操作步驟](../../guide/work-resources.md#從本機命令委派工作)。

## 閱讀與日常操作

聊天正文、輸入區、程式碼與繁中字體的間距更一致，修正輸入框遮住回覆、底部按鈕被裁切及留白過多。改善長篇富文字串流，減少反覆開關 Prompt／Canvas 時的等待與記憶體累積。

本機模型管理、對話版本與分支、Markdown 選取改寫及全文起稿繼續保留；Canvas、素材、圖片工具與 ComfyUI 生成承接前版功能。

## 下載與啟動

從 [Pre-alpha2 Release](https://github.com/gghjkiof36-wq/Prompt-Calculus-Studio/releases/tag/v0.8.10-pre-alpha.2) 下載：

| 附件 | 用途 |
|---|---|
| `PCS-v0.8.10-Pre-alpha2-source.zip` | 來源主程式、教材、素材、模板與同版 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha2-Chrome-Connector.zip` | Chrome 0.2.7 擴充與 Windows 連線工具 |
| `PCS-v0.8.10-Pre-alpha2-ComfyUI.zip` | 通用 ComfyUI 配套 |
| `PCS-v0.8.10-Pre-alpha2-ComfyUI-legacy-0.39.2.zip`、`PCS-v0.8.10-Pre-alpha2-ComfyUI-modern-0.40.0.zip` | 相容設定檔，與通用版擇一 |
| `SHA256SUMS.txt` | 下載完整性校驗 |

使用 Windows 64 位元 Python 3.12，完整解壓來源包後，在 `run.py` 旁開啟 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

之後雙擊 **`Start.vbs`**。本機 AI 需自行準備 GGUF 與支援的 `llama-server`，依[本機模型指南](../../guide/local-ai.md)設定。Chrome 保存及 ComfyUI 生成各依[Chrome 設定](../../guide/chrome-connector.md)與[相容指南](../../guide/comfyui-compatibility.md)接上配套。

## 使用前留意

- Agent 在長篇查找中可能混淆分支、漏掉條件或引用錯行，請核對來源再採用。
- Skill 目前供讀取與參考；腳本執行和專案改寫仍待後續版本。
- 本機聊天與 Agent 共用一個工作槽，忙碌時請稍後再送。外部委派目前供同一 Windows 使用者透過本機命令操作；ChatGPT／Claude 的正式任務串接仍在開發。
- 更新前備份 `files` 與 `data`，先用副本試用。對話、來源紀錄、附件及封存可能含私人內容，分享前請檢查。

[完整操作](../../guide/work-resources.md) · [驗證紀錄](validation/pre-alpha.2.md) · [歷史版本](README.md)
