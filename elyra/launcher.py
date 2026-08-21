import subprocess
import sys

# Start both processes
bot_ai = subprocess.Popen([sys.executable, "bot_ai.py"])
bot_admin = subprocess.Popen([sys.executable, "bot_admin.py"])

# Wait for either to finish (or crash)
bot_ai.wait()
bot_admin.wait()