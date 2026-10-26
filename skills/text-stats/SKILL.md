---
name: text-stats
description: Count the words, lines and characters in a text, and name the most common words. Use it when the user wants statistics about some text.
---

1. Call `text_stats` with the text.
2. Reply in one line with the word count, line count and the most common words.

The script reads the text from stdin as JSON and prints one JSON result. It cannot read files, use
the network or write anywhere: it is a pure calculation, which is the point of running it in the
sandbox.
