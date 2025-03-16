import os
import sys
import time
import curses
import argparse
import threading
import subprocess
from queue import Queue
from collections import defaultdict
from typing import List, Dict, Tuple, Optional, Any

sys.path.insert(0, os.path.dirname(__file__))
from machines_monitor.utils import read_yaml_file
from machines_monitor.dashboard import Dashboard

def main(stdscr):
    curses.curs_set(0)
    dashboard = Dashboard(stdscr, config["machines"], config["refresh_interval"])
    dashboard.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Machines Monitoring.')
    parser.add_argument('--yaml-file', type=str, default="configs/machines.yaml", help='Path to the YAML file')
    args = parser.parse_args()

    config = read_yaml_file(args.yaml_file)
    if config:
        print("Configuration loaded successfully:")

    # TODO: 当前CPU利用率还是只显示了一个core
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass