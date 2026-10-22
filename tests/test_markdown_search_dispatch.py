"""Public rich-find operations preserve navigation while coalescing refreshes."""
import json
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from PySide6.QtCore import QEvent
from PySide6.QtTest import QTest
from shiboken6 import isValid

from prompt_calculus_studio.markdown_rich_view import PreviewPage
import test_workspace_panes as fixture


class RichSearchDispatchTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events

    def setUp(self):
        fixture.WorkspacePaneTests.setUp(self)
        self.raw = b'---\ntype: note\n---\n# Search\n\nMap map map.\n\nOther target.\n'
        self.path = self.root/'Notes.md'
        self.path.write_bytes(self.raw)
        self.tab = self.w.open_path(self.path, new_tab=True)
        self.wait(lambda: not self.tab.widget.buffer.opening)
        self.surface = self.tab.widget.md_surface
        self.surface.set_mode('read')
        self.rich = self.surface.reader.rich
        self.wait(lambda: self.rich.render_state == 'ready')
        # This is functional validation, not a latency benchmark. Drain the
        # original-ready refresh before counting the next user operation.
        self.events()
        self.js('true')

    def tearDown(self):
        fixture.WorkspacePaneTests.tearDown(self)
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def wait(self, predicate):
        deadline = time.monotonic()+10
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(predicate(), 'public rich-find operation did not settle')

    def js(self, script):
        values = []
        self.rich.page().runJavaScript(script, values.append)
        self.wait(lambda: bool(values))
        return values[0]

    @contextmanager
    def observe_search(self, held=None):
        calls = []
        original = PreviewPage.runJavaScript
        page = self.rich.page()

        def invoke(target, script, *args):
            if target is page and 'window.markSearch(...' in script:
                calls.append(json.JSONDecoder().raw_decode(script.split('window.markSearch(...', 1)[1])[0])
                if held is not None and args and callable(args[-1]):
                    callback = args[-1]
                    args = (*args[:-1], lambda value: held.append((callback, value)))
            return original(target, script, *args)

        with patch.object(PreviewPage, 'runJavaScript', invoke):
            yield calls

    def assert_source_unchanged(self):
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.tab.widget.encoded(), self.raw)
        self.assertFalse(self.tab.widget.dirty)

    def test_open_query_next_same_turn_dispatches_one_effective_search(self):
        s = self.surface
        with self.observe_search() as calls:
            s.open_find();s.query.setText('map');s.find_next()
            self.wait(lambda: s.match_count.text() == '1/3')
            self.events()
            self.assertEqual(calls, [['map', False, 1]])
        self.assertEqual(json.loads(self.js(
            "JSON.stringify([...CSS.highlights.get('find-matches')].map(r=>r.toString()))")),
            ['Map', 'map', 'map'])
        self.assert_source_unchanged()

    def test_rapid_next_next_back_keeps_each_navigation_step(self):
        s = self.surface
        with self.observe_search() as calls:
            s.open_find();s.query.setText('map')
            s.find_next();s.find_next();s.find_next(-1)
            self.wait(lambda: s.match_count.text() == '1/3')
            self.events()
            self.assertEqual([call[2] for call in calls], [1, 1, -1])
        self.assert_source_unchanged()

    def test_refresh_after_navigation_keeps_the_active_match(self):
        s = self.surface
        with self.observe_search() as calls:
            s.open_find();s.query.setText('map')
            s.find_next();s.find_next();s.search_changed()
            self.wait(lambda: s.match_count.text() == '2/3')
            self.assertEqual([call[2] for call in calls], [1, 1, 0])
        self.assertEqual(self.js(
            "[...CSS.highlights.get('find-current')][0].toString()"), 'map')
        self.assert_source_unchanged()

    def test_latest_query_and_case_win_before_navigation(self):
        s = self.surface
        with self.observe_search() as calls:
            s.open_find();s.query.setText('Other')
            s.query.setText('map');s.case_sensitive.setChecked(True)
            s.query.setText('Map');s.case_sensitive.setChecked(False)
            s.case_sensitive.setChecked(True);s.find_next()
            self.wait(lambda: s.match_count.text() == '1/1')
            self.events()
            self.assertEqual(calls, [['Map', True, 1]])
        self.assertEqual(self.js(
            "[...CSS.highlights.get('find-current')][0].toString()"), 'Map')
        self.assert_source_unchanged()

    def test_query_round_trip_resets_previous_match_before_navigation(self):
        s = self.surface
        s.open_find();s.query.setText('map');s.find_next();s.find_next()
        self.wait(lambda: s.match_count.text() == '2/3')
        s.query.setText('Other');s.query.setText('map');s.find_next()
        self.wait(lambda: s.match_count.text() == '1/3')
        self.assertEqual(self.js(
            "[...CSS.highlights.get('find-current')][0].toString()"), 'Map')
        self.assert_source_unchanged()

    def test_case_round_trip_resets_previous_match_before_navigation(self):
        s = self.surface
        s.open_find();s.query.setText('map');s.find_next();s.find_next()
        self.wait(lambda: s.match_count.text() == '2/3')
        s.case_sensitive.setChecked(True);s.case_sensitive.setChecked(False);s.find_next()
        self.wait(lambda: s.match_count.text() == '1/3')
        self.assertEqual(self.js(
            "[...CSS.highlights.get('find-current')][0].toString()"), 'Map')
        self.assert_source_unchanged()

    def test_pending_query_then_close_find_clears_hidden_highlights(self):
        s = self.surface
        s.open_find();s.query.setText('map');s.find_next()
        self.wait(lambda: s.match_count.text() == '1/3')
        with self.observe_search() as calls:
            s.query.setText('Other');s.close_panel(s.find_bar)
            self.wait(lambda: bool(calls))
            self.assertFalse(self.js("CSS.highlights.has('find-matches')"))
            self.assertFalse(self.js("CSS.highlights.has('find-current')"))
            self.assertEqual(calls, [['', False, 0]])
        self.assertFalse(s.find_bar.isVisible())
        self.assert_source_unchanged()

    def test_delayed_reply_after_mode_change_cannot_overwrite_native_search(self):
        s = self.surface
        held = []
        with self.observe_search(held):
            s.open_find();s.query.setText('map');s.find_next()
            self.wait(lambda: bool(held))
            s.set_mode('write')
            before = (s.match_count.text(), s.find_previous.isEnabled(), s.find_forward.isEnabled())
            for callback, value in held:
                callback(value)
            self.assertEqual((s.match_count.text(), s.find_previous.isEnabled(), s.find_forward.isEnabled()), before)
        self.assert_source_unchanged()

    def test_delayed_reply_after_public_close_does_not_touch_destroyed_view(self):
        s = self.surface
        held = []
        with self.observe_search(held):
            s.open_find();s.query.setText('map');s.find_next()
            self.wait(lambda: bool(held))
            self.assertTrue(self.w.close_tab(self.w.index_of(self.tab.key), decision='discard',
                make_home=False, pane=self.w.pane_for(self.tab)))
            self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.events()
            self.assertFalse(isValid(self.rich))
            for callback, value in held:
                callback(value)
        self.assertEqual(self.path.read_bytes(), self.raw)
        self.assertEqual(self.errors, [])


if __name__ == '__main__':
    unittest.main()
