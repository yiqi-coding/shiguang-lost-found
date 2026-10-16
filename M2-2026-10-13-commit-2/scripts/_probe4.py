import json
import urllib.request

for name, ver in [("cryptography", "44.0.0"), ("bcrypt", "4.2.1"),
                  ("esdk-obs-python", "3.23.12"), ("httpcore", "")]:
    url = "https://pypi.org/pypi/%s/%s/json" % (name, ver) if ver else "https://pypi.org/pypi/%s/json" % name
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "probe/1.0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            d = json.loads(r.read())
        latest = d["info"]["version"]
        print("=== %s %s ===" % (name, ver or latest))
        for f in d["urls"]:
            print("    ", f["filename"])
    except Exception as e:
        print("=== %s %s FAIL: %s: %s" % (name, ver, type(e).__name__, e))
