#!/bin/sh
set -eu
mkdir -p /run/kea
exec /usr/sbin/kea-dhcp4 -c /etc/kea/kea-dhcp4.json
