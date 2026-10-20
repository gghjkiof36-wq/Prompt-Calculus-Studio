# 開始使用

[回首頁](../../README.md) · 適用：0.8.9 Alpha 1

本版預設開啟分頁工作區。完整的下載、Python 環境、文件與資料位置，以及更新注意事項見[0.8.9 開始使用](workspace-089.md)。

## 先試本機文件

1. 從內容側欄新增 Markdown 筆記，輸入文字，再切換閱讀模式。
2. 新增 Prompt 分類，保存一份文字素材；再開一份 Canvas，把素材的獨立副本加入畫布。
3. 將筆記與 Prompt 分類或 Canvas 放到左右兩邊。這些操作不需要連接 ComfyUI。

預設 `files` 保存文件，`data` 保存設定與內部資料。加入其他本機資料夾後會直接編輯其中的文件；先以副本試用。

## 接上 ComfyUI

1. 確認自己的 ComfyUI、模型與工作流原本能生成；保存工作流後關閉 ComfyUI。
2. 在「探索 → ComfyUI → 連線與工作流」安裝／更新同版 PCS 擴充。通用、legacy 與 modern 擇一，詳見[相容指南](comfyui-compatibility.md)。
3. 重新啟動後端與刷新工作流網頁，核對實際載入版本。
4. CLIP 選擇工作流文字欄位，Stage 選擇生成工作流；把 Prompt 輸出接到 CLIP，再連到 Stage。生成期間保持 PCS、後端及網頁開啟。

更多接法見[ComfyUI 整合](comfyui.md)、[Stage 參數](stage-parameters.md)及[素材與批次](prompt-materials-088.md)。

## 更新與回退

保留舊程式、擴充和原資料，新版使用獨立目錄及內容副本。新舊程式不要同時寫同一資料；完整舊資料庫導入與新機安裝仍有驗證缺口。資料格式支援不等於所有舊資料都已遷移接受，回退請用相容舊程式、舊擴充及原來的資料副本。

完整備份包含私人素材，生成 PNG 也保留必要 Prompt 與還原參照，分享前[檢查附加資訊](data-backup-privacy.md)。`SHA256SUMS.txt` 用於下載完整性核對，並非數位簽章。

0.8.8 的操作與驗證保留於[版本頁](../releases/0.8.8/README.md)。
