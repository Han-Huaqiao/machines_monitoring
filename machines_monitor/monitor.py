import time
import curses
import argparse
import threading
import subprocess
from queue import Queue
from collections import defaultdict
from typing import List, Dict, Tuple, Optional, Any


class MachineMonitor(object):
    def __init__(self, machine_info: List[Dict[str, str]], result_queue: Queue, refresh_interval: int = 2) -> None:
        self.machine = machine_info
        self.queue = result_queue
        self.ssh = None
        self.connected = False
        self.refresh_interval = refresh_interval

    def ssh_connect(self) -> None:
        """SSH免密连接"""
        # 清理旧连接
        if self.ssh and self.ssh.poll() is None:
            self.ssh.terminate()

        # 构造SSH命令（添加-T禁止伪终端）
        if "key" not in self.machine:
            # TODO: 有些机器"-T", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes" 会导致迟迟连接不上
            ssh_cmd = [
                "ssh", "-T", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking no",
                f"{self.machine['username']}@{self.machine['host']}"
            ]
        else:
            ssh_cmd = ["ssh", "-i", self.machine["key"], f"{self.machine['username']}@{self.machine['host']}"]

        # 启动SSH进程
        self.ssh = subprocess.Popen(
            ssh_cmd,
            stdin=subprocess.PIPE,
            stdout=open(os.devnull, "w"),
            # stderr=subprocess.PIPE,
            stderr=open(os.devnull, "w"),  # TODO： 有些机器会抛出一下其他信息，影响显示效果，需要处理
            text=True,
            bufsize=0  # 禁用缓冲
        )

        # 验证连接存活
        time.sleep(1)
        if self.ssh.poll() is not None:
            err = self.ssh.stderr.read()
            print(f"SSH连接失败: {err}")
            self.connected = False
            return False

        # 初始化Shell环境
        self._init_shell()
        self.connected = True
        return True

    def _init_shell(self) -> None:
        """初始化Shell环境"""
        init_commands = [
            "export LANG=C",  # 强制英文输出
            "export LC_ALL=C",
            "stty -echo",  # 禁用本地回显
            "echo 'SHELL_READY'"
        ]
        for cmd in init_commands:
            self.ssh.stdin.write(cmd + "\n")
            self.ssh.stdin.flush()

        # 等待初始化完成
        start = time.time()
        while time.time() - start < 3:
            line = self.ssh.stdout.readline()
            if "SHELL_READY" in line:
                break

    def get_remote_info(self, cmd: str) -> str:
        """改进版命令执行方法"""
        if not self.connected and not self.ssh_connect():
            return ""

        try:
            # 发送命令（统一使用 CMD_FINISHED 标记）
            full_cmd = f"{cmd} ; echo \"CMD_FINISHED_$?\"\n"  # 关键修改：移除原命令中的END_OF_COMMAND
            self.ssh.stdin.write(full_cmd)
            self.ssh.stdin.flush()

            # 读取输出
            output = []
            exit_code = -1
            start_time = time.time()

            while time.time() - start_time < 10:
                line = self.ssh.stdout.readline()
                if not line:
                    continue

                if "CMD_FINISHED_" in line:
                    code_part = line.split("_")[-1].strip()
                    try:
                        exit_code = int(code_part)
                    except ValueError:
                        exit_code = -1
                    break
                output.append(line.strip())

            # 验证执行结果
            if exit_code != 0:
                print(f"命令执行失败 (code={exit_code}): {cmd}")
                return ""

            return "\n".join(output)  # 返回清理后的输出

        except Exception as e:
            print(f"命令执行异常：{str(e)}")
            self.connected = False
            return ""

    def get_cpu_info(self) -> float:
        """获取总CPU利用率(所有核心)"""
        cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print 100 - $8}' | paste -sd+ | bc"
        output = self.get_remote_info(cmd)
        try:
            return float(output.strip())
        except:
            return 0.0

    def get_mem_info(self) -> Dict[str, float]:
        """获取内存使用量和百分比"""
        cmd = "free -b | grep Mem | awk '{print $2,$3}'"
        output = self.get_remote_info(cmd)
        try:
            total, used = map(int, output.strip().split())
            return {
                "percent": used / total * 100,
                "used_gb": round(used / (1024**3)),  # 转换为GB
                "total_gb": round(total / (1024**3))
            }
        except:
            return {"percent": 0.0, "used_gb": 0, "total_gb": 0}

    def get_gpu_info(self) -> List[Dict[str, Any]]:
        cmd = "nvidia-smi --query-gpu=index,memory.used,memory.total,utilization.gpu,temperature.gpu,power.draw --format=csv,noheader,nounits"
        output = self.get_remote_info(cmd)
        return self.parse_gpu(output)

    # def get_xpu_info(self):
    #     cmd = "xpu-smi --machine-readable"
    #     output = self.get_remote_info(cmd)
    #     return self.parse_xpu(output)
    def get_xpu_info(self) -> List[Dict[str, Any]]:
        """获取XPU信息"""
        cmd = "xpu-smi --machine-readable"
        output = self.get_remote_info(cmd)
        return self.parse_xpu(output)

    def parse_gpu(self, output) -> List[Dict[str, Any]]:
        gpus = []
        for line in output.split("\n"):
            if not line or ", " not in line:
                continue
            # 0, 60293, 81920, 13, 37, 72.39
            try:
                idx, used_mem, total_mem, util, temp, power = line.split(", ")
                gpus.append({
                    "id": int(idx),
                    "used": int(used_mem),
                    "total": int(total_mem),
                    "util": int(util),
                    "temp": int(temp),
                    "power": float(power),
                    "type": "GPU"
                })
            except:
                continue
        return gpus

    def parse_xpu(self, output) -> List[Dict[str, Any]]:
        """解析机器可读格式的XPU信息"""
        xpus = []
        for line in output.strip().split("\n"):
            parts = line.split()
            if len(parts) < 32:
                continue
            try:
                xpus.append({
                    "id": int(parts[2]),  # dev_id
                    "used": int(parts[17]),  # Memory_used
                    "total": int(parts[18]),  # Memory_size
                    "util": int(parts[19]),  # use_ratio [0,100]
                    "temp": int(parts[4]),  # temperature
                    "power": int(parts[8]),  # power(W)
                    "type": "XPU"
                })
            except Exception as e:
                print(f"解析XPU失败: {str(e)}")
        return xpus

    def get_processes(self) -> List[List[str]]:
        cmd = "ps -eo pid,user,pcpu,pmem,comm --sort=-pcpu | head -n 5"
        output = self.get_remote_info(cmd)
        processes = []
        for line in output.split("\n"):
            if line.strip() and not line.startswith("PID"):
                processes.append(line.split(maxsplit=4))
        return processes[:5]  # 返回前5个进程

    def monitor(self) -> None:
        if not self.ssh_connect():
            self.queue.put((self.machine["host"], None))
            return

        while True:
            try:
                data = {
                    "cpu": self.get_cpu_info(),
                    "mem": self.get_mem_info(),
                    "devices": self.get_gpu_info() if self.machine["type"] == "GPU" else self.get_xpu_info(),
                    "processes": self.get_processes(),
                    "timestamp": time.time()
                }
                self.queue.put((self.machine["host"], data))
            except Exception as e:
                self.queue.put((self.machine["host"], None))
                break
            time.sleep(self.refresh_interval)
