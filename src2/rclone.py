import subprocess

from pathlib import Path

class Rclone:
    name: str
    config: str

    def __init__(self, name: str, host: str, user: str, key_file: Path, config: Path, pubkey_file: Path | None = None):
        self.name = name
        self.config = config
        self.config_sftp(name, host, user, key_file, pubkey_file, config)


    def config_sftp(self, name: str, host: str, user: str, key_file: Path, pubkey_file: Path, config: Path) -> None:
        if pubkey_file:
            subprocess.run(["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, "pubkey_file", pubkey_file, f"--config={config}"])
        else:
            subprocess.run(["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, f"--config={config}"])


    def lsf(self, remote_path: str) -> None:
        subprocess.run(["rclone" "lsf" f"{self.name}:{remote_path}" f"--config={self.config}"])


    def copy(self, local_path: str, remote_path: str) -> None:
        subprocess.run(["rclone", "copy", local_path, f"{self.name}:{remote_path}", "--checksum", "--progress", f"--config={self.config}"])


    def delete(self, local_path: str) -> None:
        subprocess.run(["rclone", "delete", local_path])
