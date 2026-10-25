#!/bin/sh
set -eu

: "${BACKEND_HOSTPORT:?BACKEND_HOSTPORT must point to the backend service}"
case "$BACKEND_HOSTPORT" in
    *[!A-Za-z0-9._:-]*|:*|*:) echo "Invalid BACKEND_HOSTPORT" >&2; exit 1 ;;
esac

envsubst '${BACKEND_HOSTPORT}' \
    < /etc/nginx/default.conf.template \
    > /etc/nginx/conf.d/default.conf
