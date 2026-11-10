#!/bin/sh
# Generated content is inserted below this preflight/transaction implementation.
set -eu
umask 077
fail() { echo "$*" >&2; exit 2; }
cosmesh_root=${COSMESH_SKILLS_DIR:-"$HOME/.agents/skills"}
case "$cosmesh_root" in /*) ;; *) fail 'Use an absolute Skill directory.';; esac
cosmesh_parent=$cosmesh_root
while [ "$cosmesh_parent" != / ]; do
    [ ! -L "$cosmesh_parent" ] || fail 'Preserve symlinked Skill paths; choose a real directory.'
    cosmesh_parent=${cosmesh_parent%/*}; [ -n "$cosmesh_parent" ] || cosmesh_parent=/
done
mkdir -p "$cosmesh_root"
cosmesh_lock="$cosmesh_root/.cosmesh-ssh-install.lock"
mkdir "$cosmesh_lock" 2>/dev/null || fail 'Skill install busy. Inspect a stale .cosmesh-ssh-install.lock before retrying.'
cosmesh_stage=$(mktemp -d "$cosmesh_root/.cosmesh-ssh-stage.XXXXXX")
cosmesh_committed=no
cosmesh_moved=''
cleanup() {
    cosmesh_result=$?
    trap - EXIT HUP INT TERM
    if [ "$cosmesh_committed" != yes ]; then
        for cosmesh_name in $cosmesh_moved; do
            if [ -d "$cosmesh_stage/new-$cosmesh_name" ]; then
                : # New directory was not activated.
            elif [ -d "$cosmesh_root/$cosmesh_name" ]; then
                mv "$cosmesh_root/$cosmesh_name" "$cosmesh_stage/failed-$cosmesh_name" || cosmesh_result=2
            fi
            if [ -d "$cosmesh_stage/old-$cosmesh_name" ]; then
                mv "$cosmesh_stage/old-$cosmesh_name" "$cosmesh_root/$cosmesh_name" || cosmesh_result=2
            fi
        done
    fi
    if [ "$cosmesh_committed" = yes ] || [ "$cosmesh_result" -eq 0 ]; then
        rm -rf "$cosmesh_stage"
    else
        echo "Install stopped; preserved staging: $cosmesh_stage" >&2
    fi
    rmdir "$cosmesh_lock" 2>/dev/null || :
    exit "$cosmesh_result"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM
checksum() {
    if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
    elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
    else fail 'A SHA-256 utility (shasum or sha256sum) is required.'; fi
}
for cosmesh_name in cosmesh cosbase; do
    cosmesh_current="$cosmesh_root/$cosmesh_name"
    [ ! -L "$cosmesh_current" ] || fail 'Preserve existing Skill symlinks.'
    if [ -e "$cosmesh_current" ]; then
        [ -d "$cosmesh_current" ] && [ -f "$cosmesh_current/SHA256SUMS" ] && [ ! -L "$cosmesh_current/SHA256SUMS" ] || fail 'Existing Skill is unmanaged or native. Preserve it; use --dest for an isolated install.'
        [ "$(wc -l < "$cosmesh_current/SHA256SUMS" | tr -d ' ')" = 4 ] || fail 'Modified Skill manifest; preserve the current Skill.'
        for cosmesh_file in SKILL.md scripts/run.sh connection version; do
            [ -f "$cosmesh_current/$cosmesh_file" ] && [ ! -L "$cosmesh_current/$cosmesh_file" ] || fail 'Modified Skill resources; preserve the current Skill.'
            cosmesh_hash=$(awk -v file="$cosmesh_file" '$2 == file {print $1}' "$cosmesh_current/SHA256SUMS")
            [ "$cosmesh_name" = cosbase ] || [ "$(checksum "$cosmesh_current/$cosmesh_file")" = "$cosmesh_hash" ] || fail 'User-modified Skill preserved; review before updating.'
        done
        # Extra files and symlinked directories are user material too.
        [ "$(find "$cosmesh_current" -mindepth 1 | wc -l | tr -d ' ')" = 6 ] || fail 'Extra Skill material preserved; review before updating.'
        [ -d "$cosmesh_current/scripts" ] && [ ! -L "$cosmesh_current/scripts" ] || fail 'Preserve linked Skill resources.'
    fi
    mkdir -p "$cosmesh_stage/new-$cosmesh_name/scripts"
done

# A retired SSH Skill is archived outside the Agent Skill search root.
if [ -d "$cosmesh_root/cosbase" ]; then
    mkdir -p "$cosmesh_root/../cosmesh-skill-backups"
    cosmesh_backup=$(mktemp -d "$cosmesh_root/../cosmesh-skill-backups/retired.XXXXXX")
    cp -R "$cosmesh_root/cosbase" "$cosmesh_backup/cosbase"
    echo "Retired Skill backup: $cosmesh_backup/cosbase"
fi

# GENERATED_FILES

for cosmesh_name in cosmesh; do
    cosmesh_new="$cosmesh_stage/new-$cosmesh_name"
    for cosmesh_file in SKILL.md scripts/run.sh connection version; do
        chmod 600 "$cosmesh_new/$cosmesh_file"
        [ "$(checksum "$cosmesh_new/$cosmesh_file")" = "$(awk -v file="$cosmesh_file" '$2 == file {print $1}' "$cosmesh_new/SHA256SUMS")" ] || fail 'Generated Skill checksum mismatch.'
    done
    chmod 700 "$cosmesh_new/scripts/run.sh"
done
for cosmesh_name in cosmesh cosbase; do
    cosmesh_moved="$cosmesh_name $cosmesh_moved"
    if [ -d "$cosmesh_root/$cosmesh_name" ]; then mv "$cosmesh_root/$cosmesh_name" "$cosmesh_stage/old-$cosmesh_name"; fi
    if [ "$cosmesh_name" = cosmesh ]; then mv "$cosmesh_stage/new-$cosmesh_name" "$cosmesh_root/$cosmesh_name"; fi
done
cosmesh_committed=yes
echo 'Installed: cosmesh (SSH mode). Start a new Agent session.'
