"""Bounded streaming from existing read-only dataset mounts."""
from __future__ import annotations

import json
from pathlib import PurePosixPath

import docker

from app.application.services.dataset_s3 import S3Error
from app.core.config import get_settings
from app.domain.models.dataset import DatasetStorageType
from app.infrastructure.external.sandbox.dataset_mount_validator import canonical_host_source, docker_host_source_and_candidates
from app.infrastructure.external.sandbox.node_health import LOCAL_DEFAULT_NODE_ID
from app.interfaces.schemas.dataset import dataset_response


def resolve_file(dataset, key):
    if not key or key.startswith("/") or "\\" in key or any(p in {"", ".", ".."} for p in key.split("/")) or any(ord(c) < 32 for c in key):
        raise S3Error("InvalidArgument", "Invalid object key", 400)
    matches = []
    for item in dataset.files:
        single = dataset.model_copy(update={"files": [item]})
        public = dataset_response(single).files
        if public and public[0].path == key:
            matches.append(item)
    if len(matches) != 1:
        raise S3Error("NoSuchKey", "File is missing or its relative path is ambiguous", 404)
    item = matches[0]
    for loc in dataset.locations:
        if not loc.verified or loc.node_id != LOCAL_DEFAULT_NODE_ID:
            continue
        if loc.storage_type == DatasetStorageType.MANAGED_UPLOAD:
            return ("volume", get_settings().dataset_managed_volume, f"{dataset.dataset_id}/{item.path}")
        prefix = f"sources/{loc.location_id}/"
        if item.path.startswith(prefix):
            relative = item.path[len(prefix):]
            name = loc.mount_name or PurePosixPath(loc.source_path).name
            if relative.startswith(name + "/"):
                relative = relative[len(name) + 1:]
            return ("bind", loc.source_path, relative)
    raise S3Error("ServiceUnavailable", "Dataset is not available on the local download node", 503)


_READER = r'''
import os,sys,json,stat
parts=json.loads(sys.argv[1])
if not parts or any(p in ('','.','..') or '/' in p or '\\' in p for p in parts):
    sys.exit(1)
fd=os.open('/dataset',os.O_RDONLY|os.O_DIRECTORY)
try:
    for i,p in enumerate(parts):
        child=os.open(p,os.O_RDONLY|os.O_NOFOLLOW|(os.O_DIRECTORY if i<len(parts)-1 else os.O_NONBLOCK),dir_fd=fd)
        os.close(fd);fd=child
    st=os.fstat(fd)
    if not stat.S_ISREG(st.st_mode): sys.exit(1)
    if sys.argv[2]=='stat':
        print(json.dumps(dict(size=st.st_size,mtime=st.st_mtime,version=f'{st.st_ino:x}-{st.st_size:x}-{st.st_mtime_ns:x}')))
    else:
        if sys.argv[5] != f'{st.st_ino:x}-{st.st_size:x}-{st.st_mtime_ns:x}': sys.exit(2)
        os.lseek(fd,int(sys.argv[3]),0);left=int(sys.argv[4])
        while left:
            data=os.read(fd,min(left,1024*1024))
            if not data: sys.exit(2)
            sys.stdout.buffer.write(data);left-=len(data)
finally: os.close(fd)
'''


class FileReader:
    def __init__(self, target):
        self.client = None
        self.container = None
        self.target = target

    def open(self):
        settings = get_settings()
        kind, source, relative = self.target
        parts = relative.split("/")
        if any(p in {"", ".", ".."} for p in parts):
            raise S3Error("InvalidArgument", "Invalid file path", 400)
        try:
            self.client = docker.from_env(timeout=60)
            if kind == "bind":
                candidate, roots = docker_host_source_and_candidates(source, settings.dataset_host_path_allowlist, settings.dataset_docker_host_root)
                source = canonical_host_source(self.client, image=settings.sandbox_image, source=candidate, candidate_roots=roots)
            self.parts = json.dumps(parts)
            self.container = self.client.containers.run(
                image=settings.sandbox_image, entrypoint="/usr/bin/python3", command=["-S", "-c", "import time;time.sleep(3600)"],
                mounts=[docker.types.Mount(target="/dataset", source=source, type=kind, read_only=True)],
                detach=True, auto_remove=True, network_disabled=True, read_only=True, user="0:0", cap_drop=["ALL"],
                # Snap Docker on the production host rejects all execs when
                # no-new-privileges is set. Keep ALL capabilities dropped,
                # network disabled and the mount/root filesystem read-only.
                mem_limit="64m", pids_limit=16,
                labels={"dataseek.component": "s3-reader"},
            )
            result = self.container.exec_run(["/usr/bin/python3", "-S", "-c", _READER, self.parts, "stat"], stderr=False)
            if result.exit_code != 0:
                raise S3Error("NoSuchKey", "File is unavailable or not a regular file", 404)
            self.info = json.loads(result.output)
            return self.info
        except S3Error:
            self.close()
            raise
        except Exception:
            self.close()
            raise S3Error("ServiceUnavailable", "Read-only dataset access is unavailable", 503) from None

    def stream(self, start, length):
        try:
            result = self.container.exec_run(["/usr/bin/python3", "-S", "-c", _READER, self.parts, "read", str(start), str(length), self.info["version"]], stream=True, stderr=False)
            received = 0
            for chunk in result.output:
                received += len(chunk)
                yield chunk
            if received != length:
                raise RuntimeError("Dataset changed during download; retry the request")
        finally:
            self.close()

    def close(self):
        if self.container is not None:
            try:
                self.container.remove(force=True)
            except Exception:
                pass
            self.container = None
        if self.client is not None:
            self.client.close()
            self.client = None


def byte_range(value, size):
    if not value:
        return 0, size, 200
    try:
        if not value.startswith("bytes=") or "," in value:
            raise ValueError()
        left, right = value[6:].split("-", 1)
        if not left:
            suffix = int(right)
            if suffix <= 0:
                raise ValueError()
            start, end = max(0, size - suffix), size - 1
        else:
            start, end = int(left), min(int(right), size - 1) if right else size - 1
        if start < 0 or start >= size or end < start:
            raise ValueError()
        return start, end - start + 1, 206
    except ValueError:
        raise S3Error("InvalidRange", "Requested range is not satisfiable", 416) from None
