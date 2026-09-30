#!/bin/sh
set -eu

python -m mulewatch.amule_config

exec s6-svscan /etc/services.d
