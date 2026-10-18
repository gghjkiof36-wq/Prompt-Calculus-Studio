"""D source relations, embedded projections and lossless property transactions."""
import tempfile,unittest
from pathlib import Path
from prompt_calculus_studio.markdown_links import Notes,links,region,relocation_edits
from prompt_calculus_studio.markdown_properties import frontmatter,property_edits
from prompt_calculus_studio.markdown_source import SourceSnapshot,edited_text
from prompt_calculus_studio.markdown_render import render


class RelationsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'draft').mkdir();(self.root/'other').mkdir()
        self.source=self.root/'draft/Start.md'
        self.source.write_text('# Start\n',encoding='utf-8')
        self.target=self.root/'Target.md'
        self.target.write_text('---\naliases: [線索, "Blue book"]\ntags: [novel/mystery]\n---\n# 起點\n第一段 ^first\n\n## 細節\n保留細節\n\n# 結尾\n最後\n',encoding='utf-8')
    def tearDown(self):self.temp.cleanup()
    def test_names_aliases_anchor_and_ambiguity(self):
        notes=Notes(self.root)
        self.assertEqual(notes.resolve('Target#細節',self.source,True),([self.target],'細節'))
        self.assertEqual(notes.resolve('線索',self.source,True)[0],[self.target])
        self.assertEqual(notes.resolve('../Target.md#^first',self.source)[0],[self.target])
        for parent in ('draft','other'):(self.root/parent/'Same.md').write_text(parent,encoding='utf-8')
        notes=Notes(self.root)
        self.assertEqual(len(notes.resolve('Same',self.target,True)[0]),2)
        self.assertEqual(notes.resolve('Same',self.source,True)[0],[self.root/'draft/Same.md'])
        text=notes.text(self.target);a,b=region(text,'起點#細節');self.assertIn('保留細節',text[a:b]);self.assertNotIn('結尾',text[a:b])
        a,b=region(text,'^first');self.assertIn('第一段',text[a:b]);self.assertNotIn('細節',text[a:b])
    def test_scanner_preserves_code_comments_and_alias_markup(self):
        text='[[Target#起點|藍書]] [標題](../Target.md#%E8%B5%B7%E9%BB%9E) ![[Target#^first]]\n`[[literal]]`\n```\n[[literal]]\n```\n%% [[literal]] %%\n'
        found=links(text);self.assertEqual(len(found),3)
        edits=relocation_edits(text,self.source,self.source,self.target,self.root/'Renamed.md',Notes(self.root))
        result=edited_text(SourceSnapshot(text,0),edits)[0]
        self.assertIn('[[Renamed#起點|藍書]]',result);self.assertIn('../Renamed.md#%E8%B5%B7%E9%BB%9E',result)
        self.assertEqual(result.count('[[literal]]'),3)
    def test_embeds_update_and_cycles_stay_bounded(self):
        text='![[Target#細節]]\n\n![[Target#^first]]\n\n[[線索|另一個名字]]'
        view=render(text,self.source,self.root)
        self.assertIn('保留細節',view.body);self.assertIn('第一段',view.body);self.assertNotIn('最後',view.body)
        self.assertIn('另一個名字',view.body);self.assertIn(self.target,view.dependencies)
        self.target.write_text('來源更新\n\n![[draft/Start]]',encoding='utf-8')
        self.source.write_text('![[Target]]',encoding='utf-8')
        view=render('![[Target]]',self.source,self.root)
        self.assertIn('來源更新',view.body);self.assertIn('循環',view.body);self.assertLess(len(view.body),4000)
    def test_embedded_code_actions_use_source_and_code_is_literal(self):
        self.target.write_text('```python\na = 1 # ^literal\n```\n\n$$x^2$$',encoding='utf-8')
        view=render('![[Target]]',self.source,self.root)
        self.assertEqual(view.codes,['a = 1 # ^literal\n'])
        self.assertTrue(view.math);self.assertEqual(view.sources[0]['path'],str(self.target))
    def test_root_boundaries_and_untrusted_markup(self):
        notes=Notes(self.root)
        for link in ('../outside.md','file:///C:/Windows/win.ini','.obsidian/config.md','%2eobsidian/config.md'):
            self.assertFalse(notes.resolve(link,self.target,True)[0],link)
        view=render('<script>alert(1)</script>\n\n[bad](javascript:alert(1))',self.source,self.root)
        self.assertNotIn('<script>',view.body);self.assertNotIn('href="javascript:',view.body)
    def test_balanced_labels_and_repeated_embeds(self):
        text='[a\\]b](Target.md) [outer [inner]](Target.md)'
        self.assertEqual(len(links(text)),2)
        changed=edited_text(SourceSnapshot(text,0),relocation_edits(text,self.source,self.source,self.target,self.root/'New.md',Notes(self.root)))[0]
        self.assertIn('[a\\]b](../New.md)',changed);self.assertIn('[outer [inner]](../New.md)',changed)
        escaped=self.root/'Target (a).md';escaped.write_text('A',encoding='utf-8')
        escaped_text=r'[a](../Target%20\(a\).md)'
        changed=edited_text(SourceSnapshot(escaped_text,0),relocation_edits(escaped_text,self.source,self.source,escaped,self.root/'Renamed.md',Notes(self.root)))[0]
        self.assertEqual(changed,'[a](../Renamed.md)')
        for index in range(4):
            (self.root/f'L{index}.md').write_text(('![[L'+str(index+1)+']]\n\n')*4,encoding='utf-8')
        (self.root/'L4.md').write_text('A'*65536,encoding='utf-8')
        view=render('![[L0]]',self.source,self.root)
        self.assertLessEqual(view.embedded_count,32);self.assertLessEqual(view.embedded_bytes,1024*1024);self.assertLess(len(view.body),1200000)
    def test_property_updates_preserve_unknown_values_and_comments(self):
        text='---\n# note\naliases: [One, "Two"]\ntags:\n  - novel/test\nstatus: draft # keep\ncount: 0\nnested:\n  value: [1, 2]\ncustom: !tag value\n---\n\nBody\n'
        _,props,error=frontmatter(text);self.assertIsNone(error)
        by={p.name:p for p in props};self.assertFalse(by['nested'].editable);self.assertFalse(by['custom'].editable)
        edits=property_edits(text,[('status','status','text','done'),('count','count','number',3),('aliases','aliases','list',['Blue','青'])])
        result=edited_text(SourceSnapshot(text,0),edits)[0]
        self.assertIn('nested:\n  value: [1, 2]\ncustom: !tag value\n',result)
        self.assertIn('status: "done" # keep',result);self.assertTrue(result.endswith('\nBody\n'))
        self.assertEqual(property_edits(text,[('count','count','number',0)]),[])
        with self.assertRaises(ValueError):property_edits(text,[('nested','nested','text','bad')])
    def test_all_basic_types_and_duplicate_fields(self):
        updates=[(None,'title','text','A: # story'),(None,'n','number','1.5'),(None,'done','checkbox',True),(None,'date','date','2026-10-18'),(None,'time','datetime','2026-10-18T20:00'),(None,'tags','tags',['#novel/test'])]
        text=edited_text(SourceSnapshot('Body',0),property_edits('Body',updates))[0]
        _,props,_=frontmatter(text);self.assertEqual([p.kind for p in props],['text','number','checkbox','date','datetime','tags'])
        self.assertEqual(props[0].value,'A: # story');self.assertEqual(props[-1].value,['novel/test'])
        _,props,_=frontmatter('---\na: 1\na: 2\n---\n');self.assertTrue(all(not p.editable for p in props))

if __name__=='__main__':unittest.main()
