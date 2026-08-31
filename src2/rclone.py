import logging
import subprocess

from typing import Literal
from pathlib import Path


def print_log(message: str, level: Literal["debug", "info", "warning", "error", "critical"] = "info") -> None:
    print(message)

    # Get the method from the attributes and call
    getattr(logging, level)(message)


class Rclone:
    name: str
    config: str

    def __init__(self, name: str, host: str, user: str, key_file: Path, config: Path, pubkey_file: Path | None = None):
        self.name = name
        self.config = config
        self.config_sftp(name, host, user, key_file, pubkey_file, config)


    def config_sftp(self, name: str, host: str, user: str, key_file: Path, pubkey_file: Path, config: Path) -> None:
        if pubkey_file:
            cmd = ["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, "pubkey_file", pubkey_file, f"--config={config}"]
            print_log(f"Running: {cmd}")
            subprocess.run(cmd)
        else:
            cmd = ["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, f"--config={config}"]
            print_log(f"Running: {cmd}")
            subprocess.run(cmd)


    def lsf(self, remote_path: str) -> None:
        cmd = ["rclone", "lsf", f"{self.name}:{remote_path}", f"--config={self.config}"]
        print_log(f"Running: {cmd}")
        subprocess.run(cmd)


    def copy(self, local_path: str, remote_path: str) -> None:
        cmd = ["rclone", "copy", f"\\\\?\{local_path}", f"{self.name}:{remote_path}/{local_path.split('/')[-1]}", "--checksum", "--progress", f"--config={self.config}"]
        print_log(f"Running: {cmd}")
        subprocess.run(cmd)


    def local_delete(self, local_path: str) -> None:
        cmd = ["rclone", "delete", f"\\\\?\{local_path}"]
        print_log(f"Running: {cmd}")
        subprocess.run(cmd)
