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
from colors import Color
from monitor import MachineMonitor


class Dashboard:
    def __init__(self, stdscr, machines: List[Dict[str, Any]], refresh_interval: int = 2):
        self.stdscr = stdscr
        self.machines = machines
        self.refresh_interval = refresh_interval
        self.queue = Queue()
        self.data = defaultdict(dict)
        self.running = True
        self.margin = 2
        self.last_update = 0
        self.last_win_size = (0, 0)

        # 初始化设置
        self.stdscr.timeout(100)
        self.init_colors()
        curses.curs_set(0)
        self.min_col_width = 55
        self.max_devices = 8

    def init_colors(self) -> None:
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(Color.RED, curses.COLOR_RED, -1)
        curses.init_pair(Color.GREEN, curses.COLOR_GREEN, -1)
        curses.init_pair(Color.YELLOW, curses.COLOR_YELLOW, -1)
        curses.init_pair(Color.BLUE, curses.COLOR_BLUE, -1)
        curses.init_pair(Color.MAGENTA, curses.COLOR_MAGENTA, -1)
        curses.init_pair(Color.CYAN, curses.COLOR_CYAN, -1)
        curses.init_pair(Color.WHITE, curses.COLOR_WHITE, -1)

    def safe_addstr(self, y, x, text, attr=0) -> None:
        """安全绘制方法（防越界）"""
        max_y, max_x = self.stdscr.getmaxyx()
        if y >= max_y or x >= max_x:
            return
        text = text[:max_x - x]
        try:
            self.stdscr.addstr(y, x, text, attr)
        except curses.error:
            pass

    def draw_util_bar(self, row: int, col: int, width: int, mem_used: int, mem_total: int, util_value: float) -> None:
        """双进度条绘制（显存占用 + 显卡利用率）"""
        # 转换单位为GB
        mem_used_gb = mem_used // 1024  # 假设原始数据是MB单位
        mem_total_gb = mem_total // 1024

        # 构建显存显示字符串
        mem_str = f"{mem_used_gb}G/{mem_total_gb}G"
        mem_percent = (mem_used / mem_total) * 100

        # 构建显卡利用率显示字符串
        util_str = f"{util_value:.0f}%"

        # 动态计算进度条宽度（为文字留出空间）
        # 总宽度分配：显存进度条占60%，显卡利用率进度条占40%
        mem_bar_width = int((width - 20) * 0.6)  # 显存进度条宽度
        util_bar_width = int((width - 20) * 0.4)  # 显卡利用率进度条宽度

        # 绘制显存进度条
        mem_fill = int(mem_bar_width * mem_percent / 100)
        mem_bar = "█" * mem_fill + " " * (mem_bar_width - mem_fill)
        self.safe_addstr(row, col, f"M[{mem_bar}] {mem_str}", curses.color_pair(Color.BLUE))

        # 绘制显卡利用率进度条
        util_fill = int(util_bar_width * util_value / 100)
        util_bar = "█" * util_fill + " " * (util_bar_width - util_fill)
        self.safe_addstr(row, col + mem_bar_width + len(mem_str) + 7, f"U[{util_bar}] {util_str}",
                         curses.color_pair(Color.MAGENTA))

    def draw_machine_block(self, start_row: int, start_col: int, width: int, host: int, info: Dict) -> int:
        """优化后的机器信息块"""
        if not info:
            return 0

        height = 0
        box_color = Color.CYAN
        max_y, max_x = self.stdscr.getmaxyx()

        # 顶部边框（完整显示IP）
        title = f"╭─ {host} ─".ljust(width - 2, '─') + "╮"
        self.safe_addstr(start_row, start_col, title, curses.color_pair(box_color))
        height += 1

        # 系统资源概览（紧凑布局）
        cpu_mem_line = f"│ CPU:{info.get('cpu', 0):5.1f}% "
        mem_info = info.get('mem', {})
        cpu_mem_line += f"MEM:{mem_info.get('used_gb', 0):3.0f}/{mem_info.get('total_gb', 0):3.0f}G "
        self.safe_addstr(start_row + height, start_col,
                         cpu_mem_line.ljust(width - 2) + "│", curses.color_pair(box_color))
        height += 1

        # 设备信息（显存占用 + 显卡利用率）
        for dev in info.get('devices', [])[:8]:
            if start_row + height >= max_y - 2:
                break

            # 设备基础信息
            dev_header = f"│ {dev['type']}{dev['id']} "
            self.safe_addstr(start_row + height, start_col, dev_header.ljust(12))

            # 双进度条绘制
            self.draw_util_bar(
                row=start_row + height,
                col=start_col + 12,
                width=width - 14,  # 调整后的宽度计算
                mem_used=dev["used"],
                mem_total=dev["total"],
                util_value=dev["util"])
            self.safe_addstr(start_row + height, start_col + width - 2, "│")
            height += 1

        # 进程信息（动态截断）
        if info.get('processes') and (start_row + height < max_y - 2):
            procs = info['processes'][:3]
            proc_str = " ".join([f"{p[4][:6]}:{float(p[2]):.1f}%" for p in procs])
            self.safe_addstr(start_row + height, start_col, f"│ {proc_str.ljust(width - 4)}│")
            height += 1

        # 底部边框
        self.safe_addstr(start_row + height, start_col, "╰" + "─" * (width - 2) + "╯", curses.color_pair(box_color))
        return height + 1

    def update_display(self) -> None:
        """优化后的布局算法"""
        self.stdscr.erase()
        max_y, max_x = self.stdscr.getmaxyx()

        # 窗口尺寸变化时强制清屏
        if (max_y, max_x) != self.last_win_size:
            self.stdscr.clear()
            self.last_win_size = (max_y, max_x)

        # 绘制标题栏
        title = " Cluster Monitor (Press Q to exit) "
        self.safe_addstr(0, (max_x - len(title)) // 2, title, curses.color_pair(Color.CYAN))

        # 动态计算布局参数
        # 根据最长IP地址调整最小列宽
        ip_length = max(len(m["host"]) for m in self.machines) + 6  # 增加IP显示空间
        min_col_width = max(ip_length, 60)  # 保证最小宽度

        # 计算列数和实际列宽
        cols = max(1, (max_x - self.margin) // (min_col_width + self.margin))
        col_width = min(min_col_width, (max_x - self.margin * (cols + 1)) // cols)

        # 初始化绘制位置
        current_row = 1  # 为标题栏留出空间
        current_col = self.margin
        drawn_machines = 0

        # 遍历所有机器数据
        for host in sorted(self.data.keys()):
            if drawn_machines >= len(self.machines):
                break

            info = self.data[host].get("latest")
            if not info:
                continue

            # 计算区块高度
            device_lines = min(len(info.get('devices', [])), self.max_devices)
            proc_lines = 1 if info.get('processes') else 0
            block_height = 3 + device_lines + proc_lines

            # 换列检查
            if current_col + col_width > max_x - self.margin:
                current_row += block_height + self.margin
                current_col = self.margin
                if current_row + block_height > max_y - 2:
                    break  # 剩余空间不足显示完整区块

            # 换页检查
            if current_row + block_height > max_y - 2:
                break  # 可添加分页逻辑，此处简单截断

            # 实际绘制机器区块
            height = self.draw_machine_block(start_row=current_row,
                                             start_col=current_col,
                                             width=col_width,
                                             host=host,
                                             info=info)

            # 更新绘制位置
            current_col += col_width + self.margin
            drawn_machines += 1

            # 自动换列
            if drawn_machines % cols == 0:
                current_row += height + self.margin
                current_col = self.margin

        # 状态栏（防溢出）
        if max_y > 1:
            status = f" Machines: {drawn_machines}/{len(self.machines)} | Update: {time.strftime('%H:%M:%S')} "
            self.safe_addstr(max_y - 1, 0, status[:max_x - 1], curses.color_pair(Color.CYAN))

        # 刷新屏幕
        curses.doupdate()

    def run(self) -> None:
        # 启动监控线程
        for machine in self.machines:
            monitor = MachineMonitor(machine, self.queue, refresh_interval=self.refresh_interval)
            thread = threading.Thread(target=monitor.monitor, daemon=True)
            thread.start()

        # 主显示循环
        while self.running:
            # 处理数据更新
            has_update = False
            while not self.queue.empty():
                host, data = self.queue.get()
                self.data[host]["latest"] = data
                self.last_update = time.time()
                has_update = True

            # 强制刷新周期
            current_time = time.time()
            if has_update or (current_time - self.last_update) >= self.refresh_interval:
                self.update_display()
                self.last_update = current_time

            # 输入处理
            try:
                key = self.stdscr.getch()
                if key == ord('q'):
                    self.running = False
            except:
                pass

            time.sleep(0.1)
