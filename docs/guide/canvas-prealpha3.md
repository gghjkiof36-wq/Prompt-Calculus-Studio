# Pre-alpha3 的 Canvas 與更新

[來源版啟動](workspace-0810.md) · [本版更新](../releases/0.8.10/pre-alpha.3.md)

## 接上 ComfyUI

1. 先在 ComfyUI Desktop 或瀏覽器開啟要使用的工作流。
2. 關閉 ComfyUI，到 PCS「設定 → 管理與更新 → PCS 擴充」安裝同版配套。舊擴充會備份在 ComfyUI 的 `.pcs-program-backups`。
3. 重啟 ComfyUI 並重新整理工作流頁面，在 PCS 連接自己的服務網址。一般本機安裝常用 `http://127.0.0.1:8188`。
4. 在 Stage 選擇目前開啟的原生工作流。切換 Desktop 或瀏覽器操作時，核對目前綁定的頁面與工作流。
5. 同一工作流若在多個頁面同時開啟，先關閉重複頁，再重新選擇原生工作流。

## 排入與接續

按一次執行建立一批固定輸入。生成中再次按執行，可排入下一批。暫停後用「繼續」接續；取消目前項目後，其餘項目會暫停。

PCS 與 ComfyUI 重新連線後會核對原工作。看到「無法確認」時，可查詢原任務或查看詳情。結果在原圖收齊後一起顯示；來源圖可選「用這張圖當來源」，接到 ComfyUI 圖片輸入繼續使用。

## 沿用舊資料

先正常關閉舊 PCS，保留程式與資料備份。若舊版的 `files` 與 `data` 都位於同一交付資料夾，可在新版來源目錄執行下列命令，將範例換成自己的路徑。

```powershell
.\.venv\Scripts\python.exe run_workspace.py --migrate-from "D:/PCS-old" --migrate-into "D:/PCS-new"
```

成功後會輸出搬遷結果並記住新版資料位置，再以新版 `Start.vbs` 啟動。搬遷會複製資料、改寫可辨識的內部路徑。若來源包含尚未完成的工作，先核對該工作，再依需要加入 `--migrate-allow-inflight` 保留，重開後會暫停。

舊版回退使用原來的程式和資料副本。分開存放、曾搬過位置或使用其他資料目錄時，依[搬遷說明](../development/workspace-migration.md)處理來源位置。

## 排查卡頓

命令列啟動時加入 `--record-stalls` 可記錄慢事件。部分大型操作仍有卡頓，記下當時的操作、文件規模及時間，有助於後續排查。
