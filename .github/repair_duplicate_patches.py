from pathlib import Path
import re

for path in sorted(Path('.github').glob('dup-*.patch')):
    lines = path.read_text().splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith('@@ '):
            body = []
            i += 1
            while i < len(lines) and not lines[i].startswith('@@ ') and not lines[i].startswith('diff --git '):
                body.append(lines[i])
                i += 1
            old_count = sum(1 for x in body if x.startswith((' ', '-')))
            new_count = sum(1 for x in body if x.startswith((' ', '+')))
            match = re.match(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)', line)
            if not match:
                raise SystemExit(f'Invalid hunk header in {path}: {line}')
            out.append(f'@@ -{match.group(1)},{old_count} +{match.group(2)},{new_count} @@{match.group(3)}')
            out.extend(body)
        else:
            out.append(line)
            i += 1
    path.write_text('\n'.join(out) + '\n')
