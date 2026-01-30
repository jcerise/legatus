#!/bin/bash
# Start as root to fix workspace permissions, then drop to non-root user.
# Claude Code refuses --dangerously-skip-permissions when running as root.

chown -R agent:agent /workspace
exec gosu agent "$@"
