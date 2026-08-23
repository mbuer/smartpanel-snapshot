#!/bin/bash

cd /home/utility/python/smartpanel3

source /home/utility/python/smartpanel/venv/bin/activate

python compare.py snapshots/panel1.json
python compare.py snapshots/panel2.json

