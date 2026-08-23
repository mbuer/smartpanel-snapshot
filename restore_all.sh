#!/bin/bash

set -e

NORMALIZE=""

if [[ "$1" == "--normalize" ]]; then
    NORMALIZE="--normalize"
fi

cd /home/utility/python/smartpanel3

python restore.py snapshots/panel1.json $NORMALIZE
python restore.py snapshots/panel2.json $NORMALIZE
