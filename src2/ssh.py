import os
import subprocess
import paramiko

from pathlib import Path

class SSH:
    work_dir: str
    host_name: str
    key_type: str
    key_name: str
    key_path: Path
    pub_path: Path
    cert_path: Path
    user: str


    def __init__(self, work_dir: Path, host_name: str, key_type: str = "ed25519", key_name: str = "id_ed25519"):
        self.work_dir = Path(work_dir)
        self.host_name = host_name
        self.key_type = key_type
        self.key_name = key_name
        self.key_path = self.work_dir / key_name
        self.pub_path = self.work_dir / f"{key_name}.pub"
        self.cert_path = self.work_dir / f"{key_name}-cert.pub"

        self._create_key()
        self._sign_cert()

        self.user = self._get_user()


    def _create_key(self) -> None:
        subprocess.run(["ssh-keygen", "-t", self.key_type, "-f", self.key_path, "-N", ""])


    def _sign_cert(self) -> None:
        subprocess.run(["step", "ssh", "certificate", os.getlogin(), self.pub_path, "--sign", "--provisioner", "Google"])


    def _get_user(self) -> str:
        with open(self.cert_path, "rb") as f:
            return f.readline().decode().split()[-1]


    def sftp(self) -> paramiko.SFTPClient:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(hostname=self.host_name, username=self.user, key_filename=str(self.key_path))
        sftp = client.open_sftp()

        original_close = sftp.close
        def _close_with_client_close():
            try:
                original_close()
            finally:
                client.close()

        sftp.close = _close_with_client_close
        return sftp
