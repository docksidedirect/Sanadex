#!/bin/sh
cd "$(dirname "$0")"
echo "Sanadex.wanis.online - administrator password recovery"
python3 manage.py reset-admin
