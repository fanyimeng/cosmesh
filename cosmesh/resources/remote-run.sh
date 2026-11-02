#!/bin/sh
# A transport resource inside a Skill, not a local CosMesh runtime.
set -eu
cosmesh_dir=$(CDPATH= cd "$(dirname "$0")/.." && pwd -P)
cosmesh_name=${cosmesh_dir##*/}
case "$cosmesh_name" in cosmesh|cosbase) ;; *) echo 'Invalid Skill directory.' >&2; exit 2;; esac
{
    IFS= read -r cosmesh_host
    IFS= read -r cosmesh_prefix
    IFS= read -r cosmesh_bootstrap
} < "$cosmesh_dir/connection"
case "$cosmesh_host" in ''|-*|*[!a-zA-Z0-9_.-]*) echo 'Invalid SSH alias.' >&2; exit 2;; esac
quote() { printf "'"; printf '%s' "$1" | sed "s/'/'\\\\''/g"; printf "'"; }
remote() {
    cosmesh_command=''
    for cosmesh_arg do cosmesh_command="$cosmesh_command $(quote "$cosmesh_arg")"; done
    ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectTimeout=8 "$cosmesh_host" "$cosmesh_command"
}
case "${1-}" in
    --check-updates)
        [ "$#" -eq 1 ] || exit 2
        cosmesh_version=$(cat "$cosmesh_dir/version")
        remote python3 "$cosmesh_bootstrap" status --prefix "$cosmesh_prefix" --local-skill-version "$cosmesh_version"
        exit $?;;
    --update)
        [ "$#" -eq 1 ] || exit 2
        cosmesh_temp=$(mktemp -d)
        trap 'rm -rf "$cosmesh_temp"' EXIT HUP INT TERM
        remote cat "${cosmesh_bootstrap%/*}/install.sh" > "$cosmesh_temp/install.sh" || exit $?
        [ "$(head -n 1 "$cosmesh_temp/install.sh")" = '#!/bin/sh' ] || { echo 'Invalid Skill installer response.' >&2; exit 2; }
        sh "$cosmesh_temp/install.sh" "$cosmesh_host" --dest "${cosmesh_dir%/*}" --remote-prefix "$cosmesh_prefix" --bootstrap "$cosmesh_bootstrap"
        exit $?;;
esac
cosmesh_tty=-T
if [ "${1-}" = --tty ]; then cosmesh_tty=-t; shift; fi
[ "$#" -gt 0 ] || { echo 'Pass CLI arguments, --check-updates, or --update.' >&2; exit 2; }
cosmesh_command="exec $(quote "$cosmesh_prefix/bin/$cosmesh_name")"
for cosmesh_arg do cosmesh_command="$cosmesh_command $(quote "$cosmesh_arg")"; done
exec ssh "$cosmesh_tty" -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectTimeout=8 "$cosmesh_host" "$cosmesh_command"
