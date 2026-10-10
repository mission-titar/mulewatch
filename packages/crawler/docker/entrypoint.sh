#!/bin/sh
set -eu

python -m p2pwatch.amule_config

exec s6-svscan /etc/services.d
