import os
import subprocess

from pathlib import Path

class SSH:
    work_dir: str
    key_type: str
    key_name: str
    key_path: Path
    pub_path: Path
    cert_path: Path
    user: str


    def __init__(self, work_dir: Path, key_type: str = "ed25519", key_name: str = "id_ed25519"):
        self.work_dir = Path(work_dir)
        self.key_type = key_type
        self.key_name = key_name
        self.key_path = self.work_dir / key_name
        self.pub_path = self.work_dir / f"{key_name}.pub"
        self.cert_path = self.work_dir / f"{key_name}-cert.pub"

        self.create_key()
        self.sign_cert()

        self.user = self.get_user()

    def create_key(self) -> None:
        subprocess.run(["ssh-keygen", "-t", self.key_type, "-f", self.key_path, "-N", ""])

    def sign_cert(self) -> None:
        subprocess.run(["step", "ssh", "certificate", os.getlogin(), self.pub_path, "--sign", "--provisioner", "Google"])

    def get_user(self) -> str:
        with open(self.cert_path, "rb") as f:
            return f.readline().decode().split()[-1]
