"""Test helper: click the cat, type a message, press Enter. Usage: gui_drive.py "message" """

import subprocess
import sys
import time

sys.path.insert(0, ".")
from ragnarok.skills.screen.input import WindowsInput

subprocess.run([sys.executable, "scripts/click_cat.py"], check=True)
time.sleep(1.5)
driver = WindowsInput()
driver.type_text(sys.argv[1])
time.sleep(0.3)
driver.press("enter")
