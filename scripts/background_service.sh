#!/usr/bin/env bash

# Use the process start time to distinguish a live service from a reused PID.
process_start_time() {
  local stat_line
  local -a fields
  read -r stat_line 2>/dev/null < "/proc/$1/stat" || return 1
  read -r -a fields <<< "${stat_line##*) }"
  [[ ${fields[0]:-} != Z && -n ${fields[19]:-} ]] || return 1
  printf '%s\n' "${fields[19]}"
}
