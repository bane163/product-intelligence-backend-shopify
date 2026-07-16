#!/usr/bin/env python3
"""Probe a live Collabora discovery document and browser shell CSP."""

import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


base = "http://127.0.0.1:9980"
deadline = time.monotonic() + 120
discovery = b""
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(base + "/hosting/discovery", timeout=5) as response:
            discovery = response.read()
        break
    except Exception:
        time.sleep(2)
if not discovery:
    print("Collabora discovery did not become ready", file=sys.stderr)
    raise SystemExit(1)

root = ET.fromstring(discovery)
source = next(node.attrib["urlsrc"] for node in root.iter("action") if node.attrib.get("urlsrc"))
path = "/browser/" + source.split("/browser/", 1)[1].split("?", 1)[0]
url = base + path + "?WOPISrc=" + urllib.parse.quote("https://wopi.invalid/files/smoke", safe="")
with urllib.request.urlopen(url, timeout=15) as response:
    policy = response.headers.get("Content-Security-Policy", "")
    response.read(1024)
if "frame-ancestors https://app.example.com" not in policy:
    print("Viewer shell CSP is missing the allowed smoke origin", file=sys.stderr)
    raise SystemExit(1)
print("Collabora discovery and viewer shell CSP smoke passed")
