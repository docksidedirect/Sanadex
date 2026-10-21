# -*- coding: utf-8 -*-
"""Dev helper: find i18n keys referenced in code/templates that do not exist."""
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)
import i18n  # noqa: E402

PATTERNS = [
    re.compile(r"\bT\(req,\s*'([\w.]+)'"),
    re.compile(r"\bT\(req,\s*\"([\w.]+)\""),
    re.compile(r"\bt\('([\w.]+)'"),
    re.compile(r'\bt\("([\w.]+)"'),
    re.compile(r"\blabel='([\w.]+)'"),
    re.compile(r"\bsubtitle='([\w.]+)'"),
    re.compile(r"\b([\w]+_key)="),
]

MAPPING = re.compile(r"'([a-z_]+)':\s*'([a-z_][\w.]*)'")
OPTIONS = re.compile(r"\(\s*'([a-z_]+)'\s*,\s*'([a-z_][\w.]*)'\s*\)")

used = {}
for folder, _dirs, files in os.walk(BASE):
    if any(part in folder for part in ('__pycache__', '.git', 'backups', 'uploads', 'tools')):
        continue
    for name in files:
        if not name.endswith(('.py', '.html')):
            continue
        path = os.path.join(folder, name)
        text = open(path, encoding='utf-8', errors='replace').read()
        for pattern in PATTERNS:
            for match in pattern.finditer(text):
                used.setdefault(match.group(1), set()).add(os.path.relpath(path, BASE))
        for match in MAPPING.finditer(text):
            used.setdefault(match.group(2), set()).add(os.path.relpath(path, BASE))
        for match in OPTIONS.finditer(text):
            used.setdefault(match.group(2), set()).add(os.path.relpath(path, BASE))

missing = {key: files for key, files in used.items() if not i18n.has(key)}
print(f'keys referenced: {len(used)}   missing: {len(missing)}')
for key in sorted(missing):
    print(f'  {key:32s} {", ".join(sorted(missing[key]))[:90]}')
