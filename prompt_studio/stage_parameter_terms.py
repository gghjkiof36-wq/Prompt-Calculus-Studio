"""Panel resources; language never changes field keys or native enum values."""
TERMS = {
    'seed': ('種子', 'seed'), 'noise_seed': ('種子', 'noise_seed'),
    'steps': ('步數', 'steps'), 'cfg': ('CFG', 'CFG'),
    'sampler_name': ('採樣器名稱', 'sampler_name'), 'scheduler': ('調度器', 'scheduler'),
    'denoise': ('降噪', 'denoise'), 'width': ('寬度', 'width'), 'height': ('高度', 'height'),
    'batch_size': ('批量大小', 'batch_size'), 'upscale_method': ('縮放方法', 'upscale_method'),
    'scale_by': ('倍率', 'scale_by'), 'ckpt_name': ('模型', 'ckpt_name'),
    'lora_name': ('LoRA', 'lora_name'), 'strength_model': ('模型強度', 'strength_model'),
    'strength_clip': ('CLIP 強度', 'strength_clip'),
    'fixed': ('固定值', 'Fixed'), 'increment': ('遞增值', 'Increment'),
    'decrement': ('遞減值', 'Decrement'), 'randomize': ('隨機值', 'Random'),
    'fixed_hint': ('每次沿用指定數值', 'Keep the specified value'),
    'increment_hint': ('按原生時機加一', 'Increment at the native update time'),
    'decrement_hint': ('按原生時機減一', 'Decrement at the native update time'),
    'unknown_seed_mode': ('無法確認原生種子模式', 'Native seed mode unavailable'),
    'randomize_hint': ('按原生時機產生隨機值', 'Randomize at the native update time'),
    'workflow': ('選擇工作流', 'Select workflow'), 'nodes': ('節點', 'Nodes'),
    'workflow_label': ('工作流', 'Workflow'), 'loading': ('讀取原生節點…', 'Reading native nodes…'),
    'unchanged': ('未修改', 'No changes'), 'unsaved': ('修改尚未套用', 'Changes not applied'),
    'search': ('搜尋名稱、類型或 ID', 'Search name, type or ID'),
    'diff': ('檢視變更', 'Review changes'), 'apply': ('套用', 'Apply'),
    'apply_task': ('套用至此任務', 'Apply to this task'), 'close': ('關閉', 'Close'),
    'reset': ('重設為工作流值', 'Reset to workflow values'),
    'refresh': ('重新整理', 'Refresh'), 'source': ('來源', 'Source'),
    'native': ('在 ComfyUI 編輯', 'Edit in ComfyUI'),
    'sampling': ('採樣', 'Sampling'), 'latent': ('畫幅與 Latent', 'Dimensions and Latent'),
    'other': ('其他', 'Other'), 'latent_image': ('Latent圖像', 'Latent image'),
    'expand': ('展開', 'Expand'), 'collapse': ('收合', 'Collapse'),
    'search_expands_groups': ('搜尋時暫時展開符合的節點', 'Matching nodes are expanded while searching'),
    'no_parameters': ('此節點沒有可調整的參數', 'This node has no adjustable parameters'),
}


def term(key, language='zh-TW'):
    pair = TERMS.get(key)
    return pair[1 if language.lower().startswith('en') else 0] if pair else key
