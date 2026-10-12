#!/bin/zsh
# Job Hunter launcher (FR-010): двойной клик в Finder поднимает UI и планировщик.
cd "$(dirname "$0")" || exit 1
exec uv run job-hunter launch
