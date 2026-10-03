#!/bin/sh
# Deliberately foreground-only; Ctrl-C stops DHCP. Does not configure interfaces.
set -eu
if [ "$(uname -s)" != Darwin ]; then
  echo 'This helper is for macOS only.' >&2
  exit 1
fi
if [ "$(ipconfig getifaddr en7 2>/dev/null || true)" != 10.10.10.1 ]; then
  echo 'Configure en7 as 10.10.10.1/24 on the isolated ZTP network first.' >&2
  exit 1
fi
poc_dnsmasq="$(brew --prefix dnsmasq)/sbin/dnsmasq"
poc_config="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/dnsmasq.conf"
"$poc_dnsmasq" --test --conf-file="$poc_config"
echo 'Starting DHCP on en7 only. Stop with Ctrl-C.'
exec sudo "$poc_dnsmasq" --no-daemon --conf-file="$poc_config"
