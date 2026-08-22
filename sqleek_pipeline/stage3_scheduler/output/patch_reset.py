#!/usr/bin/env python3
"""
Patch Griffin reset_lv*.sh to avoid infinite loop on zombie pg_c_<port> processes.

The original code does:
    while killall -9 "$exe_name"
    do
        echo "waiting killing done..."
        sleep 1
    done

Because zombie processes cannot be killed, killall still matches them and
returns 0, so the while loop never exits.  We replace it with a bounded loop
that ignores zombies.
"""
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
text = path.read_text(encoding="utf-8")

new_block = r"""for _sqleek_i in $(seq 1 20)
do
    # Skip zombies — they cannot be killed and must not block reset.
    pkill -9 -x "$exe_name" 2>/dev/null || true
    if ps -eo stat=,comm= | awk -v n="$exe_name" '$2==n && $1 !~ /^Z/ {found=1} END{exit(found?0:1)}'; then
        echo "waiting killing done (attempt $_sqleek_i)..."
        sleep 1
    else
        break
    fi
done
"""

# Strategy 1: exact text match (handles any indentation variant by trying a few).
patterns_exact = [
    'while killall -9 "$exe_name"\ndo\n    echo "waiting killing done..."\n    sleep 1\ndone\n',
    'while killall -9 "$exe_name"\ndo\n\techo "waiting killing done..."\n\tsleep 1\ndone\n',
    'while killall -9 "$exe_name"\ndo\n  echo "waiting killing done..."\n  sleep 1\ndone\n',
]

replaced = False
for pat in patterns_exact:
    if pat in text:
        text = text.replace(pat, new_block)
        replaced = True
        print(f"[patch] exact match replaced in {path}")
        break

# Strategy 2: flexible regex fallback
if not replaced:
    regex = (
        r'while\s+killall\s+-9\s+"\$exe_name"\s*\n'
        r'\s*do\s*\n'
        r'\s*echo\s+"waiting killing done\.\.\."\s*\n'
        r'\s*sleep\s+1\s*\n'
        r'\s*done\s*\n'
    )
    new_text, n = re.subn(regex, new_block, text, flags=re.MULTILINE)
    if n > 0:
        text = new_text
        replaced = True
        print(f"[patch] regex match replaced ({n} occurrence(s)) in {path}")

if not replaced:
    print(f"[patch] WARNING: pattern not found in {path} — file left unchanged", file=sys.stderr)
    # Print the file so the caller can inspect it
    for i, line in enumerate(text.splitlines(), 1):
        print(f"  {i:3d}: {repr(line)}", file=sys.stderr)
else:
    path.write_text(text, encoding="utf-8")
    print(f"[patch] wrote {path}")
