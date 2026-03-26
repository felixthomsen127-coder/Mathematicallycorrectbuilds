import json
import urllib.parse
import urllib.request
from urllib.error import HTTPError

BASE = 'http://127.0.0.1:5055'
query = urllib.parse.urlencode({
    'champion': 'Lux',
    'simulation_type': 'both',
    'level': 18,
    'target_hp': 3200,
    'target_armor': 120,
    'target_mr': 90,
    'duration': 12,
})
url = BASE + '/api/champion-dps-simulation?' + query
try:
    with urllib.request.urlopen(url, timeout=90) as r:
        data = json.loads(r.read().decode('utf-8', errors='replace'))
    print('HTTP', r.status)
    print('keys', sorted(list(data.keys()))[:20])
    print('burst.total', ((data.get('burst') or {}).get('total')))
    print('dps.dps', ((data.get('dps') or {}).get('dps')))
except HTTPError as e:
    payload = e.read().decode('utf-8', errors='replace')
    print('HTTP', e.code, payload[:400])
except Exception as e:
    print('ERR', str(e))
