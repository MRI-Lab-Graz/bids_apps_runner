#!/usr/bin/env bash
# Sourced by start_gui.sh:  source scripts/load_site_env.sh <repo-dir>
#
# Loads site settings (PRISM_* variables; see site.env.example) from, in order
# of precedence: variables already in the environment, then the user's own
# ~/.config/bids_apps_runner/site.env, then <repo-dir>/site.env. First one set
# wins, so an explicit `export` always beats a file.
#
# The files are DATA, not code: only plain `KEY=VALUE` lines with a PRISM_
# key are read -- never `source`d, so a value like $(cmd) is just text.

_prism_load_one_site_env() {
    local file="$1" key value
    [[ -r "$file" ]] || return 0
    while IFS='=' read -r key value || [[ -n "$key" ]]; do
        key="${key#"${key%%[![:space:]]*}"}"          # trim leading space
        [[ "$key" =~ ^PRISM_[A-Z0-9_]+$ ]] || continue  # also skips comments/blank
        value="${value%\"}"; value="${value#\"}"       # optional "quotes"
        value="${value%\'}"; value="${value#\'}"
        [[ -n "${!key+x}" ]] && continue                 # already set: keep it
        export "$key=$value"
    done < "$file"
}

_prism_load_one_site_env "${HOME}/.config/bids_apps_runner/site.env"
_prism_load_one_site_env "${1:-.}/site.env"
unset -f _prism_load_one_site_env
