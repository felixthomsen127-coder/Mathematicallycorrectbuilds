import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError

BASE = 'http://127.0.0.1:5055'
for champ in ['Lux','Briar','Aatrox','Garen','Ahri','Annie']:
    q = urllib.parse.urlencode({'champion': champ})
    url = BASE + '/api/champion-scaling?' + q
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = json.loads(r.read().decode('utf-8', errors='replace'))
        ab = data.get('ability_breakdown') or {}
        print(champ, 'HTTP', r.status, 'ability_count', len(ab))
    except HTTPError as e:
        payload = e.read().decode('utf-8', errors='replace')
        print(champ, 'HTTP', e.code, payload[:180])
    except Exception as e:
        print(champ, 'ERR', str(e))
