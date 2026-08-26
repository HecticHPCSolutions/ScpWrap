import subprocess


class Rclone:
    def __init__(self, name, host, user, key_file, config, pubkey_file=None):
        self.name = name
        self.config = config
        self.config_sftp(name, host, user, key_file, pubkey_file, config)


    def config_sftp(self, name, host, user, key_file, pubkey_file, config):
        if pubkey_file:
            subprocess.run(["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, "pubkey_file", pubkey_file, f"--config={config}"])
        else:
            subprocess.run(["rclone", "config", "create", name, "sftp", "host", host, "user", user, "key_file", key_file, f"--config={config}"])


    def lsf(self, remote_path):
        subprocess.run(["rclone" "lsf" f"{self.name}:{remote_path}" f"--config={self.config}"])


    def copy(self, local_path, remote_path):
        subprocess.run(["rclone", "copy", local_path, f"{self.name}:{remote_path}", "--checksum", "--progress", f"--config={self.config}"])


    def delete(self, local_path):
        subprocess.run(["rclone", "delete", local_path])
