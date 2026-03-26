import json
import urllib.parse
import urllib.request

BASE = 'http://127.0.0.1:5055'
for champ in ['Lux','Briar','Aatrox','Garen']:
    q = urllib.parse.urlencode({'champion': champ})
    with urllib.request.urlopen(BASE + '/api/champion-scaling?' + q, timeout=60) as r:
        data = json.loads(r.read().decode('utf-8', errors='replace'))
    ab = data.get('ability_breakdown') or {}
    keys = list(ab.keys())
    print(champ, 'status=ok', 'keys=', keys[:6], 'count=', len(keys))
    if 'error' in data:
        print('  error:', data.get('error'))
