"""Public synthetic content for the A source-preservation checkpoint."""
SAMPLE = b'\xef\xbb\xbf' + (
    '---\r\ntags: [PCS, MD-A]\nextra: {preserve: true}\r\n---\n'
    '# MD 保存與編輯樣本\r\n\n'
    '這份合成文件用來檢查原文、同檔分頁、Undo／Redo 與自動儲存。\r\n'
    '下一批才加入完整排版工具與編寫／閱讀切換。\n\n'
    '<span style="font-family:Microsoft JhengHei UI;font-size:14pt;color:#4388aa">字體與顏色樣本</span>\r\n\n'
    '| 項目 | 內容 |\n| --- | --- |\n| 表格 | 中文、空格與 A \\| B |\r\n\n'
    '```python\nprint("原文 <tag> & 兩個  空格")\n```\r\n\n'
    '$$\nE = mc^2\n$$\r\n\n'
    '<!-- 未修改的註解保持原樣 -->\n'
    '最後一行包含非斷行空白：A\u00a0B\n'
).encode('utf-8')
