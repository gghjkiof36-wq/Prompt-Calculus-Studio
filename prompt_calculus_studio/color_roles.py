"""Qt-free color relationships shared by presets and future custom palettes.

Palette authors supply surfaces and semantic colors. Interaction states are
derived here, never copied into each preset or decided by a preset's name.
"""
import re
import colorsys


# Action fills are independent from the pastel colors used for readable status
# text. A custom palette may supply either seed; states still derive here.
ACTION_SEEDS = {'run_seed': '#0876d5', 'stop_seed': '#bd3942'}


def color(value):
    if not isinstance(value, str) or not re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        raise ValueError('A palette color must be an opaque #RRGGBB value')
    return value.lower()


def mix(left, right, amount):
    left, right = color(left), color(right)
    amount = max(0, min(1, amount))
    a = tuple(int(left[i:i+2], 16) for i in (1, 3, 5))
    b = tuple(int(right[i:i+2], 16) for i in (1, 3, 5))
    return '#' + ''.join(f'{round(x*(1-amount)+y*amount):02x}' for x, y in zip(a, b))


def luminance(value):
    channels = [int(color(value)[i:i+2], 16)/255 for i in (1, 3, 5)]
    linear = [c/12.92 if c <= .04045 else ((c+.055)/1.055)**2.4 for c in channels]
    return sum(c*w for c, w in zip(linear, (.2126, .7152, .0722)))


def contrast(left, right):
    lo, hi = sorted((luminance(left), luminance(right)))
    return (hi+.05)/(lo+.05)


def readable(preferred, backgrounds, minimum=4.5):
    """Retain the requested hue when possible; adjust lightness for reading.

    Contradictory surfaces (e.g. black, middle gray and white) cannot share one text
    color. Reject that foundation instead of claiming an unreadable palette is
    valid. Such a future editor must use separate surface/foreground roles.
    """
    preferred = color(preferred)
    backgrounds = tuple(color(value) for value in backgrounds)
    if min(contrast(preferred, bg) for bg in backgrounds) >= minimum:
        return preferred
    candidates = []
    for end in ('#000000', '#ffffff'):
        for step in range(1, 101):
            result = mix(preferred, end, step/100)
            if min(contrast(result, bg) for bg in backgrounds) >= minimum:
                candidates.append((step, result))
                break
    if not candidates:
        raise ValueError('Palette surfaces need compatible foreground contrast')
    return min(candidates)[1]


def interaction(background, foreground, amount):
    """Shift toward the readable foreground while keeping text readable."""
    foreground = readable(foreground, (background,))
    for step in range(100, -1, -1):
        result = mix(background, foreground, amount*step/100)
        if contrast(result, foreground) >= 4.5:
            return result
    return background


def status_light(seed, backgrounds):
    """Give a small marker saturated color, independent of pastel status text."""
    rgb=tuple(int(color(seed)[i:i+2],16)/255 for i in (1,3,5))
    hue,light,saturation=colorsys.rgb_to_hls(*rgb)
    saturation=max(.85,saturation) if saturation>.01 else saturation
    rgb=colorsys.hls_to_rgb(hue,.53,saturation)
    preferred='#'+''.join(f'{round(channel*255):02x}' for channel in rgb)
    return readable(preferred,backgrounds,3)


def derive_roles(foundation):
    t = {key: color(value) for key, value in foundation.items()}
    for key, value in ACTION_SEEDS.items():
        t.setdefault(key, value)
    surfaces = [t[key] for key in ('base', 'chrome', 'surface', 'raised', 'field', 'sidebar')]
    t['text'] = readable(t['text'], surfaces)
    for key in ('success', 'warning', 'error', 'info'):
        t[key] = readable(t[key], surfaces)
    t['secondary'] = readable(mix(t['text'], t['surface'], .25), surfaces)
    t['muted'] = readable(mix(t['text'], t['surface'], .32), surfaces)
    t['line'] = mix(t['surface'], t['text'], .24)
    t['divider'] = mix(t['surface'], t['text'], .10)
    t['control'] = mix(t['surface'], t['text'], .48)
    t['hover'] = interaction(t['surface'], t['text'], .06)
    t['selected'] = interaction(t['surface'], t['text'], .12)
    t['rail_selected'] = interaction(t['chrome'], t['text'], .16)
    t['popup_active'] = interaction(t['raised'], t['text'], .10)
    t['disabled_bg'] = interaction(t['surface'], t['text'], .035)
    t['disabled_text'] = readable(mix(t['text'], t['surface'], .40), surfaces+[t['disabled_bg']])
    t['scrollbar'] = mix(t['control'], t['base'], .45)
    t['wire'] = readable(mix(t['info'], t['text'], .35), (t['base'],), 3)
    t['connected_indicator'] = status_light(t['success'], (t['chrome'],t['chrome_solid']))
    t['canvas_body'] = mix(t['surface'], t['success'], .065)
    t['canvas_header'] = mix(t['surface'], t['success'], .145)
    t['canvas_field'] = mix(t['field'], t['success'], .025)
    t['text'] = readable(t['text'], surfaces+[t['canvas_body'], t['canvas_header'], t['selected'], t['popup_active']])
    t['on_accent'] = readable(t['text'], (t['accent'],))
    t['accent_hover'] = interaction(t['accent'], t['on_accent'], .09)
    t['accent_pressed'] = interaction(t['accent'], t['on_accent'], .14)
    for role in ('run','stop'):
        # Execution actions use a stable white label and glyph in every theme.
        # Constrain the fill rather than turning a bright seed's text black;
        # status colors and the user's general accent remain independent.
        t['on_'+role] = '#ffffff'
        t[role+'_background'] = readable(t[role+'_seed'], ('#ffffff',), 5)
        t[role+'_hover'] = readable(mix(t[role+'_background'], '#ffffff', .08), ('#ffffff',))
        t[role+'_pressed'] = mix(t[role+'_background'], '#000000', .12)
        t[role+'_border'] = t[role+'_background']
        t[role+'_disabled_background'] = mix(t['disabled_bg'], t[role+'_background'], .32)
        t['on_'+role+'_disabled'] = readable(t['disabled_text'], (t[role+'_disabled_background'],))
    t['scheme'] = 'light' if luminance(t['base']) > luminance(t['text']) else 'dark'
    return t
