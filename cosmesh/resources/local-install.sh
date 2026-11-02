#!/bin/sh
# One local-first setup for macOS/Linux. SSH is needed only to obtain/update the release.
set -eu
umask 077
fail() { echo "$*" >&2; exit 2; }
[ "$#" -ge 1 ] || fail 'Usage: sh install.sh EXISTING_SSH_ALIAS'
cosmesh_host=$1; shift
case "$cosmesh_host" in ''|-*|*[!a-zA-Z0-9_.-]*) fail 'Use an existing SSH alias.';; esac
cosmesh_prefix=${COSMESH_LOCAL_PREFIX:-"$HOME/.local/opt/cosmesh"}
cosmesh_skills=${COSMESH_SKILLS_DIR:-"$HOME/.agents/skills"}
cosmesh_source=/srv/cosmesh/agent
while [ "$#" -gt 0 ]; do
    [ "$#" -ge 2 ] || fail 'Missing option value.'
    case "$1" in
        --prefix) cosmesh_prefix=$2;;
        --dest) cosmesh_skills=$2;;
        --source) cosmesh_source=$2;;
        --remote-prefix|--bootstrap) : ;; # Accepted from the old SSH-only update entry.
        *) fail 'Unknown installer option.';;
    esac
    shift 2
done
case "$cosmesh_source" in /*) ;; *) fail 'Use an absolute server source directory.';; esac
case "$cosmesh_source" in *[!a-zA-Z0-9_./-]*) fail 'Invalid source directory.';; esac
cosmesh_temp=$(mktemp -d)
trap 'rm -rf "$cosmesh_temp"' EXIT HUP INT TERM
# The existing SSH transport uses this user's small server-side identity entry.
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ConnectTimeout=8 "$cosmesh_host" "python3 '$cosmesh_source/bootstrap.py' ensure"
ssh -T -o BatchMode=yes -o StrictHostKeyChecking=yes -o ForwardAgent=no -o ConnectTimeout=8 "$cosmesh_host" "cat '$cosmesh_source/local-first.tar.gz'" > "$cosmesh_temp/package.tar.gz"
tar -xzf "$cosmesh_temp/package.tar.gz" -C "$cosmesh_temp"
cosmesh_python=''
for candidate in "${COSMESH_BOOTSTRAP_PYTHON:-}" python3 /usr/bin/python3 python3.12 python3.11 python3.10 python3.9; do
    [ -n "$candidate" ] || continue
    if "$candidate" -c 'import sys,venv,pip,ensurepip; assert (3,9) <= sys.version_info[:2] < (3,14)' >/dev/null 2>&1; then
        cosmesh_python=$candidate; break
    fi
done
if [ -z "$cosmesh_python" ]; then
    cosmesh_runtime=${COSMESH_RUNTIME_DIR:-"$HOME/.local/opt/cosmesh-runtime"}
    mkdir -p "$cosmesh_runtime"
    if [ ! -x "$cosmesh_runtime/uv" ]; then
        curl -LsSf https://astral.sh/uv/install.sh -o "$cosmesh_temp/uv-install.sh"
        UV_UNMANAGED_INSTALL="$cosmesh_runtime" sh "$cosmesh_temp/uv-install.sh"
    fi
    UV_PYTHON_INSTALL_DIR="$cosmesh_runtime/python" UV_PYTHON_INSTALL_BIN=0 "$cosmesh_runtime/uv" python install 3.12
    cosmesh_python=$(UV_PYTHON_INSTALL_DIR="$cosmesh_runtime/python" "$cosmesh_runtime/uv" python find --managed-python 3.12)
fi
command -v git >/dev/null 2>&1 && git --version >/dev/null 2>&1 || fail 'Git is required. Ask your Agent to install Git, then rerun this installer.'
"$cosmesh_python" "$cosmesh_temp/local-first/local_bootstrap.py" --prefix "$cosmesh_prefix" --skills-dir "$cosmesh_skills" --ssh "$cosmesh_host" --source "$cosmesh_source"
