#!/bin/sh
# Run on the demo server to restore the previous Metabase site.
set -eu
cd /opt/aleph
test -f rollback/metabase.nginx
cp -a rollback/metabase.nginx /etc/nginx/sites-available/metabase
nginx -t
systemctl start metabase
systemctl reload nginx
docker compose --env-file deploy/.env -f deploy/compose.server.yml stop
printf '%s\n' 'Metabase restored; ALEPH stopped, its data volume preserved.'
