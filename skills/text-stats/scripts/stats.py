"""text_stats: JSON {"text": str, "top": int} on stdin, one JSON result on stdout."""

import json
import re
import sys
from collections import Counter

args = json.loads(sys.stdin.read())
text = args["text"]
top = int(args.get("top") or 3)
words = re.findall(r"[A-Za-z0-9']+", text.lower())
print(
    json.dumps(
        {
            "words": len(words),
            "lines": len(text.splitlines()) or (1 if text else 0),
            "characters": len(text),
            "top_words": [w for w, _ in Counter(words).most_common(top)],
        }
    )
)
