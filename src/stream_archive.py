#!/usr/bin/env python3
"""SFTP archive streaming utility."""

import argparse
import os
import sys
import json
from dataclasses import asdict

import paramiko
import tarfile
from typing import Literal, Union
from verify_archive import tar_stats
from sftp_client import connect_sftp


def iter_files(path):
    for dirpath, _, files in os.walk(path, followlinks=True):
        if not files:
            yield dirpath  # Preserve empty directories
        for f in files:
            yield os.path.join(dirpath, f)


def write_tar(f: paramiko.SFTPFile, localpath: str, mode: Literal['w', 'w:gz', 'w:bz2', 'w:xz'] = 'w:gz'):
    """
    Create a tar archive from localpath and write it directly to the file handle.
    
    Args:
        f: paramiko.SFTPFile object to write to
        localpath: Path to local directory to archive
        mode: Tar file mode for compression. Options:
              'w' - no compression
              'w:gz' - gzip compression (default)
              'w:bz2' - bzip2 compression
              'w:xz' - xz compression
    """
    print(f'Creating a tar archive from {localpath} with mode {mode}')
    
    with tarfile.open(fileobj=f, mode=mode) as tf:
        toplevel = os.path.basename(os.path.normpath(localpath))
        for file_path in iter_files(localpath):
            # Use the basename of the path to set the arcname
            arcname = os.path.join(toplevel, os.path.relpath(file_path, localpath))
            tf.add(file_path, arcname=arcname)


def infer_arctype_from_archive(archive: str) -> str:
    archive_lower = archive.lower()
    if archive_lower.endswith('.tar.gz') or archive_lower.endswith('.tgz'):
        return 'tar.gz'
    if archive_lower.endswith('.tar.bz2') or archive_lower.endswith('.tbz2'):
        return 'tar.bz2'
    if archive_lower.endswith('.tar.xz') or archive_lower.endswith('.txz'):
        return 'tar.xz'
    raise ValueError(
        f"Could not infer archive type from extension: {archive}. "
        "Provide --arctype explicitly (tar.gz, tar.bz2, tar.xz)."
    )


def create_archive(config,host,archive,source,arctype):
    try:
        resolved_arctype = arctype or infer_arctype_from_archive(archive)
        # Connect to SFTP
        sftp = connect_sftp(config, host)
        with sftp.file(archive,'w') as f:
            if resolved_arctype == "tar.gz":
                write_tar(f,source, mode='w:gz')
            elif resolved_arctype == "tar.bz2":
                write_tar(f,source, mode='w:bz2')
            elif resolved_arctype == "tar.xz":
                write_tar(f,source, mode='w:xz')
            else:
                raise ValueError(
                    f"Unsupported arctype: {resolved_arctype}. "
                    "Supported values are: tar.gz, tar.bz2, tar.xz"
                )
        sftp.close()
        
    except FileNotFoundError as e:
        print(f"Tried to open the sftp file object path {archive} but failed.")
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"SSH Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def stat_archive(config: Union[str,None], host: Union[str,None], archive: str, checksum: bool = False, output: Union[str,None] = None):
    hasher = None
    if checksum:
        import hashlib
        hasher = hashlib.md5

    arctype = infer_arctype_from_archive(archive)
    if not arctype.startswith('tar'):
        raise ValueError("stat only supports tar archives: .tar.gz/.tgz, .tar.bz2/.tbz2, .tar.xz/.txz")

    mode: Literal['r', 'r:gz', 'r:bz2', 'r:xz']
    if arctype == 'tar.gz':
        mode = 'r:gz'
    elif arctype == 'tar.bz2':
        mode = 'r:bz2'
    else:
        mode = 'r:xz'

    try:

        if config is None and host is None:
            with open(archive, 'rb') as f:
                stats = tar_stats(f, hasher=hasher, mode=mode)
        elif config is not None and host is not None:
            sftp = connect_sftp(config, host)
            with sftp.file(archive, 'r') as f:
                stats = tar_stats(f, hasher=hasher, mode=mode)
            sftp.close()

        payload = {path: asdict(file_stats) for path, file_stats in stats.items()}
        text = json.dumps(payload, indent=2, sort_keys=True)

        if output:
            with open(output, 'w', encoding='utf-8') as out_f:
                out_f.write(text)
            print(f"Wrote archive stats to {output}")
        else:
            print(text)

    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except paramiko.SSHException as e:
        print(f"SSH Error: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    """Parse arguments and handle SFTP operations."""
    parser = argparse.ArgumentParser(
        description="SFTP archive streaming utility"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    create_parser = subparsers.add_parser("create", help="Create an archive on remote host")
    create_parser.add_argument(
        "--config",
        type=str,
        default=os.path.expanduser("~/.ssh/config"),
        help="Path to SSH config file (default: ~/.ssh/config)",
    )
    create_parser.add_argument(
        "--host",
        type=str,
        required=True,
        help="Host alias from SSH config to connect to",
    )

    create_parser.add_argument(
        "--archive",
        type=str,
        required=True,
        help='Path to the archive (.tar.gz, .tar.bz2, or .tar.xz) to create on the remote server'
    )
    create_parser.add_argument(
        "--source",
        type=str,
        required=True,
        help="Source directory to archive"
    )
    create_parser.add_argument(
        "--arctype",
        type=str,
        required=False,
        default=None,
        help="The type of archive to create"
    )

    stat_parser = subparsers.add_parser("stat", help="Read remote tar archive stats")
    stat_parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to SSH config file (required with --host; omit both for local archive)",
    )
    stat_parser.add_argument(
        "--host",
        type=str,
        required=False,
        help="Host alias from SSH config to connect to (required with --config)",
    )
    stat_parser.add_argument(
        "--archive",
        type=str,
        required=True,
        help="Path to the tar archive to inspect (remote path with SFTP mode, local path otherwise)",
    )
    stat_parser.add_argument(
        "--checksum",
        action="store_true",
        help="Include md5 checksum for each file entry",
    )
    stat_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Write stats JSON output to this local file",
    )
    
    args = parser.parse_args()

    if args.command == "create":
        create_archive(args.config,args.host,args.archive,args.source,args.arctype)
    elif args.command == "stat":
        if (args.config is None) != (args.host is None):
            print("Error: --config and --host must be provided together, or both omitted for local archive stat", file=sys.stderr)
            sys.exit(1)

        if args.config is not None:
            args.config = os.path.expanduser(args.config)
        
        stat_archive(args.config, args.host, args.archive, args.checksum, args.output)




if __name__ == "__main__":
    main()
