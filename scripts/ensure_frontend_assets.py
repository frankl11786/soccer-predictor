#!/usr/bin/env python3
from pathlib import Path

p=Path("app/index.html")
if not p.exists(): raise SystemExit("app/index.html not found")
s=p.read_text()
css='<link rel="stylesheet" href="touchline-enhancements.css">'
js='<script src="touchline-enhancements.js"></script>'
if css not in s: s=s.replace("</head>",f"  {css}\n</head>")
if js not in s: s=s.replace("</body>",f"  {js}\n</body>")
p.write_text(s)
print("Frontend enhancement assets wired into app/index.html")
