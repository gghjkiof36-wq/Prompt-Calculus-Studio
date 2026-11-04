# 工作資源、Skill 與 Agent

[開始使用](workspace-0810.md) · 適用 0.8.10 Pre-alpha2

## 整理文件與對話

從「探索 → 工作資源 → 檔案」加入資料夾或對話封存。工作區內可新增資料夾、改名及拖動文件；同名項目會另取名稱。外部來源可閱讀或解除登記，編輯時先在首頁開啟副本。

ChatGPT ZIP／JSON、Codex JSONL 及 PCS 對話可先預覽，再勾選需要的對話並按「輸出 Markdown」。每份分別保存，結果列出成功與失敗項目；也可從首頁直接開啟封存匯入。輸出包含原始封存、可取得的附件及分支連結。來源格式與附件範圍見[對話保留說明](../development/work-resources.md#對話格式與保留範圍)。

## 加入 Skill

切換到「Skill」，加入含 `SKILL.md` 的完整資料夾。選取後先看簡介、版本與來源，按「查看內容」閱讀正文或找附檔。要修改時從「⋯」建立管理副本，再編輯及儲存；原來源保留。

Skill 的啟停控制整個資源庫。每個 Agent 另外保存自己的技能清單；只供手動指定的技能，使用時從聊天選單加入 `@名稱`。來源改變後先重新讀取並確認版本。

## 建立專案與角色

在「專案」建立名稱與實際工作目錄，加入共用規則及參考來源，再在專案內新增 Agent。為角色選模型、指派 Skill，填寫角色規則與專屬來源。專案設定供角色共用，Agent 自己的來源只影響該角色。

回到聊天，從「⋯ → 協作設定」選 Agent 並套用。這份聊天會使用角色模型與規則；選「一般對話」可回到原設定。可直接交代查找任務，或從「指定 Skill」加入技能。回覆旁的讀取紀錄顯示實際讀過的檔案與文字。

工具審查選「每次詢問」時，讀取內容前會等待允許或拒絕；自動審查用於已登記範圍內的本地唯讀工具。停止保留已有文字與讀取紀錄。Agent 目前適合協助查找，長篇分析及引用仍需人工核對；Skill 裡的腳本與 hooks 暫不執行。

## 從本機命令委派工作

保持 PCS 開啟，在已保存的專案按「啟用本機委派」。在來源包目錄執行：

```powershell
.\.venv\Scripts\python.exe tools/pcs_agent.py instances
.\.venv\Scripts\python.exe tools/pcs_agent.py catalog --instance INSTANCE_ID
.\.venv\Scripts\python.exe tools/pcs_agent.py submit --instance INSTANCE_ID --project PROJECT_ID --agent AGENT_ID --key UNIQUE_KEY --task-file task.txt
.\.venv\Scripts\python.exe tools/pcs_agent.py status --instance INSTANCE_ID --task-id TASK_ID
.\.venv\Scripts\python.exe tools/pcs_agent.py result --instance INSTANCE_ID --task-id TASK_ID
.\.venv\Scripts\python.exe tools/pcs_agent.py stop --instance INSTANCE_ID --task-id TASK_ID
```

先用前兩個命令取得實際 ID，再替換後續命令的對應欄位。`task.txt` 使用 UTF-8。相同工作重送時沿用同一個 `UNIQUE_KEY`，便可查回原任務。首次受理會建立 `.pcs-chat`；取結果時保留固定任務的草稿、來源與保存紀錄，請覆核後採用。

本機委派預設關閉，供同一 Windows 使用者操作。按「停止本機委派」會停止該專案的委派工作並撤回新工作與結果讀取。聊天與 Agent 共用一個工作槽，忙碌時稍後再送；工作文字上限 16,000 字元。更詳細的保存及輸出範圍見[本機委派說明](../development/local-agents.md)。

## 保存與分享

更新前備份 `files`、`data` 和外部原件。聊天內會保存角色來源位置及實際讀取文字，輸出的原始封存也可能包含私人內容；分享前依[資料與隱私指南](data-backup-privacy.md)檢查。
