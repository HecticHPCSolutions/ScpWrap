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
    config: Path

    def __init__(self, name: str, host: str, user: str, key_file: Path, config: Path, pubkey_file: Path | None = None) -> None:
        self.name = name
        self.config = config
        self._config_sftp(name, host, user, key_file, pubkey_file, config)


    def _log_run(self, cmd: list[str]) -> None: 
        print_log(f"Running: {cmd}", "debug")
        subprocess.run(cmd)


    def _config_sftp(self, name: str, host: str, user: str, key_file: Path, pubkey_file: Path, config: Path) -> None:
        if pubkey_file:
            self._log_run(["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, "pubkey_file", pubkey_file, f"--config={config}"])
            
        else:
            self._log_run(["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, f"--config={config}"])


    def lsf(self, remote_path: str) -> None:
        self._log_run(["rclone", "lsf", f"{self.name}:{remote_path}", f"--config={self.config}", "--sftp-known-hosts-file=none"])


    def mkdir(self, remote_path: str) -> None:
        self._log_run(["rclone", "mkdir", f"{self.name}:{remote_path}", f"--config={self.config}", "--sftp-known-hosts-file=none"])


    def copy(self, local_path: str, remote_path: str) -> None:
        self._log_run(["rclone", "copy", f"\\\\?\\{local_path}", f"{self.name}:{remote_path}/{local_path.split('/')[-1]}", "--checksum", "--progress", f"--config={self.config}", "--sftp-known-hosts-file=none"])


    def archive(self, local_path: str, remote_path: str, format: Literal["zip", "tar", "tar.gz", "tar.bz2", "tar.lz", "tar.lz4", "tar.xz", "tar.zst", "tar.br", "tar.sz", "tar.mz"] = "tar.gz") -> None:
        self._log_run(["rclone", "archive", "create", f"\\\\?\\{local_path}", f"{self.name}:{remote_path}/{local_path.split('/')[-1]}.{format}", "--checksum", "--progress", "--format", format,  f"--config={self.config}", "--sftp-known-hosts-file=none"])


    def local_delete(self, local_path: str) -> None:
        self._log_run(["rclone", "delete", f"\\\\?\\{local_path}"])
