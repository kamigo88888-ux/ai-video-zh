"""Exercise the AI integration path (prompt building, annotated frames, JSON parsing,
applying results to the job) with a mocked Claude response."""
import json, sys, types, glob
sys.path.insert(0, '.')
from app import ai, jobs, settings

jid = sorted(p.split('/')[-2] for p in glob.glob(str(settings.JOBS / '*/job.json')))[0]
jobs.load_all()
job = jobs.get(jid)
captured = {}

def fake_create(**kw):
    captured.update(kw)
    items = []
    for t in job['tracks']:
        cap = t['text'] not in ('ACME', 'PowerCord Pro', 'USB-C 65W')
        items.append({'id': t['id'], 'is_caption': cap, 'corrected_english': t['text'],
                      'translation': ('測試翻譯' + str(t['id'])) if cap else ''})
    body = json.dumps({'items': items}) if 'items' in json.dumps(kw['output_config']) else json.dumps({'translation': '單句測試'})
    return types.SimpleNamespace(stop_reason='end_turn', content=[types.SimpleNamespace(type='text', text=body)])

ai._create = fake_create
ai.available = lambda: True
an = jobs._load_analysis_for_ai(job)
jobs._apply_ai(job, an, jobs.info_of(job))
content = captured['messages'][0]['content']
imgs = [c for c in content if c['type'] == 'image']
print('model', captured['model'], '| images sent:', len(imgs), '| schema ok:', captured['output_config']['format']['type'])
print(content[-1]['text'][:400])
for t in job['tracks']:
    print(t['id'], t['enabled'], t['text'], '->', t['translation'])
# save one annotated frame for inspection
import base64
open(sys.argv[1], 'wb').write(base64.b64decode(imgs[0]['source']['data']))
print('translate_one ->', ai.translate_one('Only $19.99 today', ['a', 'b'], 'humor', 'shorter'))
