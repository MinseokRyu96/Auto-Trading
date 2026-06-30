#!/bin/zsh
set -euo pipefail

cd /Users/minseokryu/Desktop/Project/GoStop
exec /Library/Frameworks/Python.framework/Versions/3.14/bin/python3 -m gostop.cli market-runner --live --confirm-live --interval-minutes 5 --sleep-seconds 60 --start-time 09:05 --end-time 15:20
