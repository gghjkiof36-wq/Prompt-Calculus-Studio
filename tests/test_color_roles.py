"""Custom foundations share readable derived roles without Qt or preset names."""
import copy
import unittest

from prompt_studio.color_roles import color, contrast, derive_roles, luminance, readable
from prompt_studio.theme import visual_tokens


FOUNDATIONS = {
    'dark_red': {
        'base': '#190F13', 'chrome': '#27171D', 'surface': '#302127',
        'raised': '#3C2B32', 'field': '#211419', 'sidebar': '#23151B',
        'text': '#F9EEF1', 'success': '#A4CDB5', 'warning': '#EDD098',
        'error': '#F4A8B7', 'info': '#B9CCE9', 'accent': '#DDA0B5',
        'accent_neutral': '#DBC8BB', 'accent_blue': '#ABBFDA',
        'accent_green': '#A6C6AF', 'chrome_solid': '#27171D',
        'mica_tint': '#362329', 'acrylic_tint': '#403037',
    },
    'light_purple': {
        'base': '#F5F0FA', 'chrome': '#E8E0EF', 'surface': '#FBF8FE',
        'raised': '#ECE4F5', 'field': '#F8F4FC', 'sidebar': '#E8E0F1',
        'text': '#312B39', 'success': '#355E4B', 'warning': '#735016',
        'error': '#94394E', 'info': '#3C5285', 'accent': '#724987',
        'accent_neutral': '#685A6D', 'accent_blue': '#445B88',
        'accent_green': '#42654F', 'chrome_solid': '#E8E0EF',
        'mica_tint': '#DBD0E4', 'acrylic_tint': '#DDD5EA',
    },
}
SURFACES = ('base', 'chrome', 'surface', 'raised', 'field', 'sidebar')


class ColorRoleTests(unittest.TestCase):
    def test_complete_custom_foundations_derive_without_preset_names(self):
        for name, foundation in FOUNDATIONS.items():
            with self.subTest(name=name):
                roles = derive_roles(foundation)
                self.assertEqual(roles['base'], foundation['base'].lower())
                self.assertEqual(roles['scheme'], 'dark' if name == 'dark_red' else 'light')
                for foreground in ('text', 'secondary', 'muted', 'success', 'warning', 'error', 'info'):
                    for surface in SURFACES:
                        self.assertGreaterEqual(contrast(roles[foreground], roles[surface]), 4.5,
                                                (name, foreground, surface))
                for surface in ('chrome','chrome_solid'):
                    self.assertGreaterEqual(contrast(roles['connected_indicator'],roles[surface]),3)

    def test_light_and_dark_connection_markers_are_vivid_and_keep_status_hue(self):
        import colorsys
        for name in ('graphite','paper','mist'):
            roles=visual_tokens({'visual_palette':name})
            def hls(key):
                return colorsys.rgb_to_hls(*(int(roles[key][i:i+2],16)/255 for i in (1,3,5)))
            before,after=hls('success'),hls('connected_indicator')
            self.assertAlmostEqual(before[0],after[0],delta=.01)
            self.assertGreater(after[2],before[2])
            self.assertGreaterEqual(after[2],.75)
            self.assertLessEqual(after[1],.55)
            for surface in ('chrome','chrome_solid'):
                self.assertGreaterEqual(contrast(roles['connected_indicator'],roles[surface]),3)

    def test_hover_and_active_shift_toward_foreground_on_their_own_surface(self):
        for name, foundation in FOUNDATIONS.items():
            roles = derive_roles(foundation)
            for active, surface in (('hover', 'surface'), ('selected', 'surface'),
                                    ('rail_selected', 'chrome'), ('popup_active', 'raised')):
                with self.subTest(name=name, active=active):
                    start, end = luminance(roles[surface]), luminance(roles['text'])
                    actual = luminance(roles[active])
                    self.assertGreater((actual-start)*(end-start), 0)
                    self.assertLess(abs(actual-start), abs(end-start))
                    self.assertGreaterEqual(contrast(roles['text'], roles[active]), 4.5)
            self.assertGreater(abs(luminance(roles['selected'])-luminance(roles['surface'])),
                               abs(luminance(roles['hover'])-luminance(roles['surface'])))

    def test_accent_and_semantic_actions_keep_readable_text_in_all_states(self):
        for name, foundation in FOUNDATIONS.items():
            roles = derive_roles(foundation)
            for foreground, backgrounds in (
                ('on_accent', ('accent', 'accent_hover', 'accent_pressed')),
                ('on_run', ('run_background', 'run_hover', 'run_pressed')),
                ('on_stop', ('stop_background', 'stop_hover', 'stop_pressed')),
            ):
                for background in backgrounds:
                    with self.subTest(name=name, background=background):
                        self.assertGreaterEqual(contrast(roles[foreground], roles[background]), 4.5)

    def test_action_seeds_are_shared_and_customizable_without_status_overrides(self):
        for foundation in FOUNDATIONS.values():
            original=derive_roles(foundation)
            changed=visual_tokens(foundation=dict(foundation, run_seed='#be46e8', stop_seed='#d36418'))
            for role in ('run','stop'):
                self.assertNotEqual(original[role+'_background'],changed[role+'_background'])
                for state in ('background','hover','pressed'):
                    self.assertGreaterEqual(contrast(changed['on_'+role],changed[role+'_'+state]),4.5)
            self.assertEqual(original['info'],changed['info'])
            self.assertEqual(original['error'],changed['error'])

    def test_input_maps_and_cached_results_are_independent(self):
        for name, foundation in FOUNDATIONS.items():
            original = copy.deepcopy(foundation)
            settings = {'visual_palette': 'paper', 'accent': 'green'}
            saved_settings = dict(settings)
            roles = visual_tokens(settings, foundation=foundation)
            self.assertEqual(roles['accent'], foundation['accent'].lower())
            roles['hover'] = '#000000'
            self.assertEqual(foundation, original)
            self.assertEqual(settings, saved_settings)
            self.assertEqual(visual_tokens(settings, foundation=foundation)['hover'],
                             derive_roles(foundation)['hover'])

    def test_public_foundation_rejects_derived_override(self):
        for key in ('hover', 'selected', 'popup_active', 'on_accent', 'canvas_body'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                visual_tokens(foundation={key: '#123456'})

    def test_malformed_or_transparent_colors_are_rejected(self):
        for invalid in ('#fff', '#12345678', '123456', '#12GG56', ' #123456',
                        '#123456\n', 'transparent', 'rgb(1,2,3)', '', None, 123456):
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                color(invalid)
            foundation = dict(FOUNDATIONS['dark_red'], base=invalid)
            with self.subTest(foundation=invalid), self.assertRaises(ValueError):
                derive_roles(foundation)

    def test_readable_retains_valid_preferred_color(self):
        self.assertEqual(readable('#E8D5DD', ('#190f13', '#302127')), '#e8d5dd')
        adjusted = readable('#777777', ('#ffffff',))
        self.assertGreaterEqual(contrast(adjusted, '#ffffff'), 4.5)

    def test_incompatible_surfaces_are_rejected_without_silent_fallback(self):
        # Black and white alone DO share a narrow 4.5:1 middle-gray range.
        shared = readable('#777777', ('#000000', '#ffffff'))
        self.assertGreaterEqual(min(contrast(shared, surface) for surface in ('#000000', '#ffffff')), 4.5)
        with self.assertRaises(ValueError):
            readable('#777777', ('#000000', '#ffffff'), minimum=7)
        with self.assertRaises(ValueError):
            readable('#777777', ('#000000', '#777777', '#ffffff'))
        foundation = dict(FOUNDATIONS['dark_red'], base='#000000',
                          raised='#777777', field='#ffffff')
        with self.assertRaises(ValueError):
            derive_roles(foundation)


if __name__ == '__main__':
    unittest.main()
