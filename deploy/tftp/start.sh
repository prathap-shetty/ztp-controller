#!/bin/sh
set -eu
: "${TFTP_BIND_ADDRESS:?Set the Linux ZTP interface address}"
exec /usr/sbin/in.tftpd --foreground --listen --secure --user tftp \
  --address "${TFTP_BIND_ADDRESS}:69" --port-range 40000:40100 \
  --blocksize 1468 /srv/bootstrap
