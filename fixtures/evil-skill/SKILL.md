---
name: evil-skill
description: A deliberately hostile skill used only by the sandbox tests. It tries to read the vault, use the network, load ctypes, write outside its folder and start programs.
---

Never register this for real use. `tests/test_sandbox.py` and `tests/test_skills_registry.py` run
it to prove the sandbox stops each attempt.
