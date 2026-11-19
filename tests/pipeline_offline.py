"""Offline pipeline test: detect -> (manual translations) -> layout -> render, then verify output."""
import sys, time, json, subprocess
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parent.parent))
from app.video import probe
from app.detect import analyze
from app.layout import build_layout, DEFAULT_STYLE
from app.render import render
from app import ai

src = sys.argv[1]; dst = sys.argv[2]
info = probe(src)
t = time.time()
a = analyze(src, info)
print('analyze', round(time.time() - t, 1), 's')
ZH = {"This gadget changes everything": "這小物根本改變人生", "No more tangled cables ever again!": "從此跟打結的線\n說掰掰！",
      "Only $19.99 today": "今天只要 $19.99", "Link in bio, grab yours now": "連結在主頁，手刀搶起來"}
for tr in a['tracks']:
    tr['is_caption'] = tr['text'] in ZH
    tr['enabled'] = tr['is_caption']
    tr['translation'] = ZH.get(tr['text'], '')
    tr['override'] = {}
    print(tr['id'], tr['text'], tr['start_frame'], tr['end_frame'], 'heuristic=', ai.heuristic_is_caption(tr, info.height))
lay = build_layout(a['tracks'], DEFAULT_STYLE, info.width, info.height)
for l in lay:
    print(' layout', l['id'], l['lines'], l['font_px'], l['position'], l['keyframes'][:2], len(l['keyframes']))
t = time.time()
render(src, dst, info, lay)
print('render', round(time.time() - t, 1), 's')
print(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_name,width,height,r_frame_rate,nb_frames,bit_rate',
                      '-of', 'compact', dst], capture_output=True, text=True).stdout)
