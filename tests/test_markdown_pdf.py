import time
import unittest
from PySide6.QtCore import QThreadPool
from PySide6.QtPdf import QPdfDocument
from prompt_calculus_studio.markdown_pdf import PdfExport
import test_workspace_panes as fixture


class MarkdownPdfTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events
    def tearDown(self):
        self.w.notebook.stop();QThreadPool.globalInstance().waitForDone(3000)
        fixture.WorkspacePaneTests.tearDown(self)
    def export(self,text,race=False):
        source=self.root/'Notes.md';raw=text.encode('utf-8');source.write_bytes(raw)
        surface=self.open('Notes.md').widget.md_surface
        output=self.root/'output.pdf';job=PdfExport(surface,output);result=[]
        job.finished.connect(lambda ok,message:result.append((ok,message)))
        if race:output.write_bytes(b'external edit')
        deadline=time.monotonic()+25
        while not result and time.monotonic()<deadline:
            self.app.processEvents();time.sleep(.015)
        self.assertTrue(result,'PDF renderer did not finish')
        self.assertEqual(source.read_bytes(),raw)
        return output,result[0]
    def test_text_table_formula_diagram_and_footnote(self):
        output,result=self.export('# Title\n\nChinese 中文\n\n| Scene | Status |\n| --- | --- |\n| Bookshop | Done |\n\n$$E=mc^2$$\n\n```mermaid\nflowchart LR\n A[Start] --> B[End]\n```\n\nText[^n]\n\n[^n]: Footnote\n')
        self.assertTrue(result[0],result[1]);pdf=QPdfDocument();self.assertEqual(pdf.load(str(output)),QPdfDocument.Error.None_)
        text='\n'.join(pdf.getAllText(n).text() for n in range(pdf.pageCount()))
        for expected in ('Chinese','Scene','Bookshop','Start','End','Footnote'):self.assertIn(expected,text)
        self.assertNotIn('flowchart LR',text);self.assertGreater(pdf.pageCount(),0);pdf.close()
    def test_missing_image_refuses_incomplete_pdf(self):
        output,result=self.export('# A\n\n![Missing](missing.png)\n')
        self.assertFalse(result[0]);self.assertFalse(output.exists());self.assertIn('找不到',result[1])
    def test_external_output_change_is_preserved(self):
        output,result=self.export('# A\n\nContent\n',True)
        self.assertFalse(result[0]);self.assertEqual(output.read_bytes(),b'external edit')


if __name__=='__main__':unittest.main()
