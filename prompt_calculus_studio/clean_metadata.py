"""Container-level metadata inspection and removal, independent of image codecs.

PNG: https://www.w3.org/TR/png-3/
WebP: https://developers.google.com/speed/webp/docs/riff_container
Only rendering data is allowed through. Text-bearing metadata is never copied.
"""
import json
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

PNG=b'\x89PNG\r\n\x1a\n'
BLOCK=65536
TEXT_LIMIT=1024*1024
MAX_FILE=512*1024*1024
PNG_IMAGE={b'IHDR',b'PLTE',b'IDAT',b'IEND',b'tRNS',b'acTL',b'fcTL',b'fdAT'}
PNG_COLOR={b'iCCP',b'gAMA',b'cHRM',b'sRGB',b'pHYs',b'sBIT',b'cICP',b'mDCv',b'cLLi'}
WEBP_IMAGE={b'VP8X',b'VP8 ',b'VP8L',b'ALPH',b'ANIM',b'ANMF'}

@dataclass
class Part:
    kind: bytes
    start: int
    size: int
    payload: int
    length: int
    prefix: bytes=b''

def checked_read(stream,n):
    data=stream.read(n)
    if len(data)!=n: raise ValueError('圖片結構不完整。')
    return data

def format_of(path):
    with Path(path).open('rb') as stream: head=stream.read(12)
    if head.startswith(PNG): return 'png'
    if head.startswith(b'\xff\xd8'): return 'jpeg'
    if head[:4]==b'RIFF' and head[8:12]==b'WEBP': return 'webp'
    if head[:2]==b'BM': return 'bmp'
    raise ValueError('不支援此圖片格式；支援 PNG、JPEG、WebP、BMP。')

def png_parts(stream,end):
    stream.seek(8); first=True; image=False
    while stream.tell()<end:
        start=stream.tell(); length,kind=struct.unpack('>I4s',checked_read(stream,8))
        if start+12+length>end: raise ValueError('PNG 區塊長度超出檔案。')
        if first and (kind!=b'IHDR' or length!=13): raise ValueError('PNG 缺少有效 IHDR。')
        if kind not in PNG_IMAGE and not kind[0]&32: raise ValueError('PNG 含未知必要區塊，不能安全清理。')
        first=False; crc=zlib.crc32(kind); prefix=bytearray(); remaining=length
        while remaining:
            block=checked_read(stream,min(BLOCK,remaining)); crc=zlib.crc32(block,crc); remaining-=len(block)
            limit=TEXT_LIMIT if kind in (b'tEXt',b'zTXt',b'iTXt') else BLOCK
            if kind!=b'IDAT' and len(prefix)<limit: prefix.extend(block[:limit-len(prefix)])
        expected=struct.unpack('>I',checked_read(stream,4))[0]
        if crc & 0xffffffff != expected: raise ValueError('PNG 校驗失敗，原圖可能損壞。')
        yield Part(kind,start,length+12,start+8,length,bytes(prefix))
        if kind==b'IDAT': image=True
        if kind==b'IEND':
            if length or not image: raise ValueError('PNG 圖片區塊不完整。')
            if stream.tell()<end: yield Part(b'TRAILER',stream.tell(),end-stream.tell(),stream.tell(),end-stream.tell())
            return
    raise ValueError('PNG 缺少結束區塊。')

def entropy_end(stream,end):
    """Find an unescaped marker across JPEG scan buffer boundaries."""
    while stream.tell()<end:
        base=stream.tell(); data=stream.read(min(BLOCK,end-base)); index=0
        while True:
            at=data.find(b'\xff',index)
            if at<0: break
            if at==len(data)-1:
                stream.seek(base+at)
                if base+at+1>=end: raise ValueError('JPEG 掃描資料未結束。')
                break
            code=data[at+1]
            if code==0 or 0xd0<=code<=0xd7: index=at+2; continue
            stream.seek(base+at); return base+at
    raise ValueError('JPEG 缺少結束標記。')

def jpeg_parts(stream,end):
    stream.seek(2); saw_scan=False
    while stream.tell()<end:
        start=stream.tell()
        if checked_read(stream,1)!=b'\xff': raise ValueError('JPEG 標記損壞。')
        code=checked_read(stream,1)[0]
        while code==0xff: code=checked_read(stream,1)[0]
        kind=bytes([code])
        if code==0xd9:
            if not saw_scan: raise ValueError('JPEG 缺少影像資料。')
            yield Part(kind,start,stream.tell()-start,stream.tell(),0)
            if stream.tell()<end: yield Part(b'TRAILER',stream.tell(),end-stream.tell(),stream.tell(),end-stream.tell())
            return
        if code==0x01 or 0xd0<=code<=0xd8 or code==0: raise ValueError('JPEG 標記位置無效。')
        length=struct.unpack('>H',checked_read(stream,2))[0]-2; payload=stream.tell()
        if length<0 or payload+length>end: raise ValueError('JPEG 區塊長度無效。')
        prefix=checked_read(stream,length) if 0xe0<=code<=0xef or code==0xfe else b''
        stream.seek(payload+length)
        yield Part(kind,start,stream.tell()-start,payload,length,prefix)
        if code==0xda:
            saw_scan=True; start=stream.tell(); stop=entropy_end(stream,end)
            yield Part(b'SCAN',start,stop-start,start,stop-start)
    raise ValueError('JPEG 缺少結束標記。')

def riff_parts(stream,start,end):
    stream.seek(start)
    while stream.tell()<end:
        pos=stream.tell(); kind,length=struct.unpack('<4sI',checked_read(stream,8))
        if pos+8+length+(length&1)>end: raise ValueError('WebP 區塊長度無效。')
        prefix=checked_read(stream,min(length,BLOCK)) if kind not in (b'VP8 ',b'VP8L',b'ALPH',b'ANMF') else b''
        stream.seek(pos+8+length+(length&1))
        yield Part(kind,pos,8+length+(length&1),pos+8,length,prefix)

def webp_parts(stream,end):
    stream.seek(4); declared=struct.unpack('<I',checked_read(stream,4))[0]+8
    if declared>end or declared<12: raise ValueError('WebP 檔案長度無效。')
    image=False
    for part in riff_parts(stream,12,declared):
        if part.kind in (b'VP8 ',b'VP8L',b'ANMF'): image=True
        yield part
    if not image: raise ValueError('WebP 缺少影像區塊。')
    if declared<end: yield Part(b'TRAILER',declared,end-declared,declared,end-declared)

def parts(path):
    path=Path(path); size=path.stat().st_size
    if size>MAX_FILE: raise ValueError('單張圖片超過 512 MB，請先縮小或另用專用工具。')
    fmt=format_of(path)
    with path.open('rb') as stream:
        if fmt=='png': yield from png_parts(stream,size)
        elif fmt=='jpeg': yield from jpeg_parts(stream,size)
        elif fmt=='webp': yield from webp_parts(stream,size)
        else: raise ValueError('BMP 需重新編碼後清理。')

def orientation(data):
    if data.startswith(b'Exif\0\0'): data=data[6:]
    try:
        endian={b'II':'<',b'MM':'>'}[data[:2]]
        if struct.unpack_from(endian+'H',data,2)[0]!=42: return 1
        offset=struct.unpack_from(endian+'I',data,4)[0]; count=struct.unpack_from(endian+'H',data,offset)[0]
        for i in range(min(count,4096)):
            at=offset+2+i*12; tag,typ,n=struct.unpack_from(endian+'HHI',data,at)
            if tag==0x112 and typ==3 and n==1:
                value=struct.unpack_from(endian+'H',data,at+8)[0]; return value if 1<=value<=8 else 1
    except (KeyError,ValueError,struct.error): pass
    return 1

def label_for(fmt,part):
    k=part.kind
    if fmt=='png':
        if k in (b'tEXt',b'zTXt',b'iTXt'): return k.decode()+': '+part.prefix.split(b'\0',1)[0].decode('latin1')
        return {b'eXIf':'EXIF',b'iCCP':'ICC Profile',b'TRAILER':'檔尾附加資料'}.get(k,k.decode('ascii','replace'))
    if fmt=='webp': return {b'ICCP':'ICC Profile',b'EXIF':'EXIF',b'XMP ':'XMP',b'TRAILER':'檔尾附加資料'}.get(k,k.decode('ascii','replace'))
    if k==b'TRAILER': return '檔尾附加資料'
    if k==b'\xfe': return 'Comment'
    if k==b'\xe1': return 'EXIF' if part.prefix.startswith(b'Exif\0\0') else 'XMP / APP1'
    if k==b'\xe2' and part.prefix.startswith(b'ICC_PROFILE\0'): return 'ICC Profile'
    if k==b'\xed': return 'IPTC / Photoshop'
    return 'APP'+str(k[0]-0xe0)

def image_part(fmt,part):
    if fmt=='png': return part.kind in PNG_IMAGE
    if fmt=='webp': return part.kind in WEBP_IMAGE
    if part.kind in (b'\xe0',b'\xee'):
        return (part.kind==b'\xe0' and part.prefix.startswith(b'JFIF\0') and part.length==14 and part.prefix[7:]==b'\0\0\1\0\1\0\0') or (part.kind==b'\xee' and part.prefix.startswith(b'Adobe') and part.length==12)
    return part.kind==b'SCAN' or (len(part.kind)==1 and part.kind[0]<0xe0) or part.kind==b'\xd9'

def allowed(fmt,part,mode):
    if image_part(fmt,part): return True
    if mode!='generation': return False
    return (fmt=='png' and part.kind in PNG_COLOR) or (fmt=='webp' and part.kind==b'ICCP') or (fmt=='jpeg' and ((part.kind==b'\xe2' and part.prefix.startswith(b'ICC_PROFILE\0')) or (part.kind==b'\xe0' and part.prefix.startswith(b'JFIF\0'))))

def text_value(part):
    try:
        data=part.prefix
        if part.length>len(data): return '[內容過大，未展開]'
        key,data=data.split(b'\0',1); compressed=False
        if part.kind==b'zTXt': data=data[1:]; compressed=True
        elif part.kind==b'iTXt':
            flag=data[0]; data=data[2:].split(b'\0',2)[-1]
            compressed=bool(flag)
        if compressed:
            decoder=zlib.decompressobj(); data=decoder.decompress(data,TEXT_LIMIT)
            if not decoder.eof: return '[內容過大或壓縮資料不完整，僅顯示前段]\n'+data.decode('utf-8','replace')
        value=data.decode('utf-8','replace')
        try: return json.loads(value)
        except ValueError: return value
    except (ValueError,IndexError,zlib.error): return '[無法展開此欄位]'

def inspect(path,expand=True):
    fmt=format_of(path); result=dict(format=fmt,metadata=[],text={},orientation=1,animated=False,icc=False)
    if fmt=='bmp':
        result['metadata']=['BMP 標頭／色彩資料（匯出時重新建立）']; return result
    for part in parts(path):
        if not image_part(fmt,part): result['metadata'].append(label_for(fmt,part))
        if part.kind in (b'acTL',b'ANMF'): result['animated']=True
        if part.kind in (b'iCCP',b'ICCP') or (part.kind==b'\xe2' and part.prefix.startswith(b'ICC_PROFILE\0')): result['icc']=True
        if part.kind in (b'eXIf',b'EXIF') or (part.kind==b'\xe1' and part.prefix.startswith(b'Exif\0\0')):
            result['orientation']=orientation(part.prefix)
        if expand and fmt=='png' and part.kind in (b'tEXt',b'iTXt',b'zTXt') and len(result['text'])<256:
            result['text'][part.prefix.split(b'\0',1)[0].decode('latin1')]=text_value(part)
        if fmt=='webp' and part.kind==b'ANMF':
            if part.length<16: raise ValueError('WebP 動畫影格標頭不完整。')
            with Path(path).open('rb') as stream:
                for child in riff_parts(stream,part.payload+16,part.payload+part.length):
                    if child.kind not in (b'ALPH',b'VP8 ',b'VP8L'): result['metadata'].append('Frame: '+child.kind.decode('ascii','replace'))
    return result

def copy_range(source,out,start,length):
    source.seek(start)
    while length:
        data=checked_read(source,min(BLOCK,length)); out.write(data); length-=len(data)

def write_riff(out,kind,data):
    out.write(kind+struct.pack('<I',len(data))+data)
    if len(data)&1: out.write(b'\0')

def sanitize(source,destination,mode):
    if mode not in ('all','generation'): raise ValueError('Metadata 清理模式無效。')
    fmt=format_of(source); records=list(parts(source))
    with Path(source).open('rb') as stream,Path(destination).open('wb') as out:
        out.write(PNG if fmt=='png' else b'\xff\xd8' if fmt=='jpeg' else b'RIFF\0\0\0\0WEBP')
        for part in records:
            if fmt=='jpeg' and part.kind==b'\xe0' and part.prefix.startswith(b'JFIF\0'):
                # JFIF colour interpretation is structural; thumbnails are not.
                data=part.prefix[:14]
                if len(data)<14: raise ValueError('JFIF 標頭不完整。')
                data=data[:12]+b'\0\0'
                if mode=='all': data=data[:7]+b'\0\0\1\0\1\0\0'
                out.write(b'\xff\xe0'+struct.pack('>H',len(data)+2)+data); continue
            if fmt=='jpeg' and part.kind==b'\xee' and part.prefix.startswith(b'Adobe'):
                data=part.prefix[:12]
                if len(data)!=12: raise ValueError('Adobe 色彩標頭不完整。')
                data=data[:7]+b'\0\0\0\0'+data[11:12]
                out.write(b'\xff\xee\0\x0e'+data); continue
            if not allowed(fmt,part,mode): continue
            if fmt=='webp' and part.kind==b'VP8X':
                if part.length!=10: raise ValueError('VP8X 標頭長度無效。')
                data=bytearray(part.prefix); data[0]&=0x12; data[1:4]=b'\0'*3
                if mode=='generation' and any(p.kind==b'ICCP' for p in records): data[0]|=0x20
                write_riff(out,part.kind,data)
            elif fmt=='webp' and part.kind==b'ANMF':
                if part.length<16: raise ValueError('WebP 動畫影格標頭不完整。')
                start=out.tell(); out.write(b'ANMF\0\0\0\0'); stream.seek(part.payload)
                header=bytearray(checked_read(stream,16)); header[15]&=3; out.write(header)
                for child in list(riff_parts(stream,part.payload+16,part.payload+part.length)):
                    if child.kind in (b'ALPH',b'VP8 ',b'VP8L'):
                        copy_range(stream,out,child.start,8+child.length)
                        if child.length&1: out.write(b'\0')
                end=out.tell(); length=end-start-8; out.seek(start+4); out.write(struct.pack('<I',length)); out.seek(end)
                if length&1: out.write(b'\0')
            elif fmt=='webp':
                copy_range(stream,out,part.start,8+part.length)
                if part.length&1: out.write(b'\0')
            else: copy_range(stream,out,part.start,part.size)
        if fmt=='webp':
            end=out.tell(); out.seek(4); out.write(struct.pack('<I',end-8))

def verify(path,mode):
    fmt=format_of(path)
    if fmt=='bmp':
        with Path(path).open('rb') as stream: header=stream.read(54)
        if len(header)!=54 or struct.unpack_from('<I',header,14)[0]!=40 or header[6:10]!=b'\0'*4:
            raise ValueError('BMP 並非可驗證的乾淨標頭。')
        declared=struct.unpack_from('<I',header,2)[0]; offset=struct.unpack_from('<I',header,10)[0]
        width,height,planes,bits,compression=struct.unpack_from('<iiHHI',header,18)
        if width<=0 or height==0 or planes!=1 or bits not in (24,32) or compression or offset!=54:
            raise ValueError('BMP 含未驗證的色彩表或附加標頭。')
        if declared!=54+((width*bits+31)//32*4)*abs(height) or declared!=Path(path).stat().st_size:
            raise ValueError('BMP 含未驗證的附加資料。')
        if header[46:54]!=b'\0'*8 or (mode=='all' and header[38:46]!=b'\0'*8):
            raise ValueError('BMP 仍含附加資訊。')
        return []
    records=list(parts(path)); unwanted=[label_for(fmt,p) for p in records if not allowed(fmt,p,mode)]
    if fmt=='webp':
        for p in records:
            if p.kind==b'VP8X' and (p.length!=10 or p.prefix[0]&(0xed if mode=='all' else 0xcd) or p.prefix[1:4]!=b'\0'*3):
                unwanted.append('VP8X Metadata／保留欄位')
    details=inspect(path)
    unwanted.extend(v for v in details['metadata'] if v.startswith('Frame: '))
    if unwanted: raise ValueError('仍含指定移除的 Metadata：'+', '.join(unwanted))
    return details['metadata']
