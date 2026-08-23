#!/bin/bash

cd /home/utility/python/smartpanel3

source /home/utility/python/smartpanel/venv/bin/activate

python save.py 10.85.226.96 panel1
python save.py 10.85.226.72 panel2
