import os

import paramiko


def connect_sftp(config: str, host: str) -> paramiko.SFTPClient:
    ssh_config = paramiko.SSHConfig()
    with open(os.path.expanduser(config), "r", encoding="utf-8") as f:
        ssh_config.parse(f)

    host_config = ssh_config.lookup(host)
    hostname = host_config.get("hostname", host)
    username = host_config.get("user")
    port = int(host_config.get("port", 22))

    key_filenames = host_config.get("identityfile")
    if key_filenames is not None:
        key_filenames = [os.path.expanduser(path) for path in key_filenames]

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if key_filenames is not None:
        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            key_filename=key_filenames[0],
            allow_agent=True,
            look_for_keys=True,
        )
    else:
        client.connect(
            hostname=hostname,
            port=port,
            username=username,
            allow_agent=True,
            look_for_keys=True,
        )
    sftp = client.open_sftp()

    original_close = sftp.close

    def close_with_client_close():
        try:
            original_close()
        finally:
            client.close()

    sftp.close = close_with_client_close
    return sftp
