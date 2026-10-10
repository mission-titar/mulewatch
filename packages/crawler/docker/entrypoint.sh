#!/bin/sh
set -eu

python -m p2pwatch_amule.config

exec s6-svscan /etc/services.d
