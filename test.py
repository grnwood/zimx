 import re, sys, pathlib
 p = pathlib.Path("/home/grnwood/Desktop/Hibbett/HibbettOMS/FluentConnect/1.0-Authentication/1.0-Authentication.md")
 text = p.read_text(encoding="utf-8")
 hits = [c for c in text if ord(c) > 0xFFFF]
 print("non-BMP count:", len(hits), "sample:", hits[:10])

