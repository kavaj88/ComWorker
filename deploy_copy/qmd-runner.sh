#!/bin/sh
# QMD wrapper - use comworker home for cache to ensure persistence across restarts
# Memory stored at /root/.comworker/memory/ — shared by all agents
export HOME=/root
export COMWORKER_HOME=/root/.comworker
exec qmd "$@"
