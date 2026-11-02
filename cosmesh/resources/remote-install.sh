#!/bin/sh
# Run on the user's computer. Only shell utilities and existing SSH are needed.
set -eu
umask 077
fail() { echo "$*" >&2; exit 2; }
quote() { printf "'"; printf '%s' "$1" | sed "s/'/'\\\\''/g"; printf "'"; }
[ "$#" -ge 1 ] || fail 'Usage: sh install.sh SSH_ALIAS [--dest SKILL_ROOT] [--remote-prefix SERVER_PREFIX]'
cosmesh_host=$1; shift
case "$cosmesh_host" in ''|-*|*[!a-zA-Z0-9_.-]*) fail 'Use an existing SSH alias.';; esac
cosmesh_destination=${COSMESH_SKILLS_DIR:-"$HOME/.agents/skills"}
cosmesh_prefix=''
cosmesh_bootstrap=/srv/cosmesh/agent/bootstrap.py
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || fail 'Missing installer option value.'
    case "$1" in
        --dest) cosmesh_destination=$2;;
        --remote-prefix) cosmesh_prefix=$2;;
        --bootstrap) cosmesh_bootstrap=$2;;
        *) fail 'Unknown installer option.';;
    esac
    shift 2
done
remote() {
    cosmesh_command=''
    for cosmesh_arg do cosmesh_command="$cosmesh_command $(quote "$cosmesh_arg")"; done
    ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectTimeout=8 "$cosmesh_host" "$cosmesh_command"
}
cosmesh_temp=$(mktemp -d)
trap 'rm -rf "$cosmesh_temp"' EXIT HUP INT TERM
if [ -n "$cosmesh_prefix" ]; then
    remote python3 "$cosmesh_bootstrap" ensure --prefix "$cosmesh_prefix" || fail 'Server setup failed; local Skills were preserved.'
    cosmesh_entry="exec $(quote "$cosmesh_prefix/bin/cosmesh")"
else
    remote python3 "$cosmesh_bootstrap" ensure || fail 'Server setup failed; local Skills were preserved.'
    cosmesh_entry='exec "$HOME/.local/opt/cosmesh/bin/cosmesh"'
fi
cosmesh_entry="$cosmesh_entry skills export --ssh $(quote "$cosmesh_host") --bootstrap $(quote "$cosmesh_bootstrap")"
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectTimeout=8 "$cosmesh_host" "$cosmesh_entry" > "$cosmesh_temp/skills.sh" || fail 'Skill export failed; local Skills were preserved.'
[ "$(head -n 1 "$cosmesh_temp/skills.sh")" = '#!/bin/sh' ] || fail 'Invalid export response; local Skills were preserved.'
COSMESH_SKILLS_DIR="$cosmesh_destination" sh "$cosmesh_temp/skills.sh" || fail 'Skill update incomplete. The server may already be updated; existing local material was preserved.'
echo 'Ready: two SSH Skills installed. Open a new Agent session to verify discovery.'
