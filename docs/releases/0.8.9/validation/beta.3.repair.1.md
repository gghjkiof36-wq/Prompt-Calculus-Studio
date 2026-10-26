# v0.8.9 Beta3 repair1 本機修正版

2026-10-26。依使用者指定，維持 Beta3 並標示 repair1；為可直接執行的私人 Windows 完整資料夾，未公開發布。建置旗標為 `--v0.8.9-beta.3.repair.1`，啟動行為沿用 Beta3。

## 修改內容

- Canvas 加入素材可按外側／Esc 關閉，側欄可同次點選切換及滑入。
- files 隱藏為內容根；立即建立未命名檔案／資料夾，重名加序號。
- 增加重點色、自訂 HEX 與色盤。
- 組合直接輸入可從 PNG metadata 匯入明確正面 Prompt；多來源選取，既有文字追加／取代／取消。
- 素材編輯卡片高度、管理分頁留白、文字變體工具列與切換更新調整。
- 起始組合改列內釘選；組合去重移至排除 Tag 框右下。
- 管理統一左下，組合 Prompt 預覽並列；類型選單右靠。
- 空心及實心圖釘統一順時針傾斜 20°，套用所有共用圖示入口。

## 證據邊界

功能與版面實作固定於 `30e874a7097aab1b3292e70691f76bffeaf1e911`；後續加入 repair1 建置識別及共用圖釘角度調整。最終來源／binary 身份以包內 BUILD_INFO.json 為準，檔案雜湊見 PACKAGE_MANIFEST.json。

本輪有界來源證據共 229 項通過，包含最終 layout／dialog regression／management UI 37 項全綠；各功能完成後未受後續改動影響的證據沿用。保留三組可在公開 Beta3 基準重現的舊測試失敗：Canvas picker 4、prompt group UI 1、large text controls 1；不宣稱全測試通過。

先前相同產品實作的 R15 包已通過 manifest、ZIP CRC 及隔離 offscreen EXE 啟動／公式 Markdown 閱讀。repair1 名稱版另以其最終產物回執核對。未做原生 Windows DPI／IME／合成器動畫、真實服務或 GPU 驗收，類型切換瞬間閃爍仍待實際操作確認。
