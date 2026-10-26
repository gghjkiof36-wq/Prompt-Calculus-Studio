"""Persisted accent choices shared by desktop and Qt-free document readers."""
import re

ACCENT_PRESETS = (
    ('跟隨配色', 'neutral', None),
    ('霧藍', 'blue', '#a6c6db'),
    ('柔綠', 'green', '#b8cbb1'),
    ('暖黃', 'yellow', '#e9c66d'),
    ('粉紅', 'pink', '#f077af'),
    ('珊瑚橘', 'orange', '#ee987a'),
    ('薰衣紫', 'purple', '#bb9be5'),
    ('青綠', 'teal', '#71cbbd'),
    ('白色', 'white', '#ffffff'),
)
ACCENT_NAMES = tuple(value for _, value, _ in ACCENT_PRESETS) + ('custom',)
DEFAULT_CUSTOM_ACCENT = '#f077af'


def valid_hex(value):
    return isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value) is not None
