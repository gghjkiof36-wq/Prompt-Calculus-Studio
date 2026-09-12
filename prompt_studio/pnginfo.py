"""Bounded PNG text inspection without loading pixels or Qt."""
import json
import struct
import zlib
from pathlib import Path


def png_metadata(path):
    result = {}
    budget = 8 * 1024 * 1024
    with Path(path).open('rb') as stream:
        if stream.read(8) != b'\x89PNG\r\n\x1a\n':
            return dict(source='none', raw={}, nodes=[], note='不是標準 PNG，未讀取內嵌資料。')
        while header := stream.read(8):
            if len(header) != 8:
                break
            length, kind = struct.unpack('>I4s', header)
            if kind == b'IEND':
                break
            if kind not in (b'tEXt', b'zTXt', b'iTXt') or length > budget:
                stream.seek(length + 4, 1)
                continue
            raw = stream.read(length)
            stream.read(4)
            budget -= length
            try:
                key, data = raw.split(b'\0', 1)
                if key not in (b'prompt', b'workflow', b'parameters', b'prompt_studio'):
                    continue
                if kind == b'zTXt':
                    if data[0] != 0:
                        continue
                    data = zlib.decompressobj().decompress(data[1:], 2 * 1024 * 1024 + 1)
                elif kind == b'iTXt':
                    compressed, method = data[:2]
                    _, _, data = data[2:].split(b'\0', 2)
                    if compressed:
                        if method != 0:
                            continue
                        data = zlib.decompressobj().decompress(data, 2 * 1024 * 1024 + 1)
                if len(data) > 2 * 1024 * 1024:
                    continue
                text = data.decode('utf-8')
                result[key.decode()] = json.loads(text) if key != b'parameters' else text
            except (ValueError, IndexError, zlib.error, UnicodeError):
                continue
    extracted = []
    graph = result.get('prompt')
    if isinstance(graph, dict):
        for key, node in graph.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get('inputs', {})
            if not isinstance(inputs, dict):
                continue
            useful = {k: v for k, v in inputs.items() if k in (
                'text', 'ckpt_name', 'unet_name', 'lora_name', 'strength_model', 'strength_clip',
                'seed', 'noise_seed', 'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise')
                and not isinstance(v, list)}
            if useful:
                extracted.append(dict(node=key, type=node.get('class_type', ''), values=useful))
    return dict(source='embedded' if result else 'none', raw=result, nodes=extracted,
        note='圖片內嵌資料；列出節點原值，多個採樣器不會擅自合併。' if result else '圖片沒有可辨識的內嵌參數。')
