#!/bin/zsh
set -euo pipefail

cd /Users/minseokryu/Desktop/Project/GoStop
exec /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m gostop.cli dashboard --host 127.0.0.1 --port 8765
