"""Descriptor-relative reads: no symlinks, writes, devices or full-file copies."""
import heapq
import os
import stat

from .errors import S3Error


def parts(key, allow_empty=False):
    if allow_empty and key == '':
        return []
    try:
        length = len(key.encode()) if isinstance(key, str) else 0
    except UnicodeError:
        raise S3Error('InvalidArgument', 'Object key must be valid UTF-8', 400) from None
    if not isinstance(key, str) or not key or length > 4096 or key.startswith('/') or '\\' in key or any(ord(c) < 32 for c in key):
        raise S3Error('InvalidArgument', 'Invalid relative object path', 400)
    result = key.split('/')
    if any(p in {'', '.', '..'} for p in result):
        raise S3Error('InvalidArgument', 'Invalid relative object path', 400)
    return result


def open_relative(root, key='', directory=False):
    components = parts(key, allow_empty=directory)
    fd = None
    try:
        fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        for i, item in enumerate(components):
            child = os.open(item, os.O_RDONLY | os.O_NOFOLLOW | (os.O_DIRECTORY if directory or i < len(components)-1 else os.O_NONBLOCK), dir_fd=fd)
            os.close(fd)
            fd = child
        if not directory and not stat.S_ISREG(os.fstat(fd).st_mode):
            raise S3Error('NoSuchKey', 'Object is not a regular file', 404)
        return fd
    except (OSError, S3Error):
        if fd is not None:
            os.close(fd)
        raise S3Error('NoSuchKey', 'Object or directory is unavailable', 404) from None


def metadata(st):
    return {'size': st.st_size, 'mtime': st.st_mtime, 'etag': f'"{st.st_dev:x}-{st.st_ino:x}-{st.st_size:x}-{st.st_mtime_ns:x}"'}


class Reader:
    def __init__(self, root, key):
        self.fd = open_relative(root, key)
        self.info = metadata(os.fstat(self.fd))

    def stream(self, start, length):
        try:
            os.lseek(self.fd, start, os.SEEK_SET)
            while length:
                data = os.read(self.fd, min(length, 1024 * 1024))
                if not data:
                    raise RuntimeError('Source changed during download')
                length -= len(data)
                yield data
        finally:
            self.close()

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def byte_range(value, size):
    if not value:
        return 0, size, 200
    try:
        if not value.startswith('bytes=') or ',' in value:
            raise ValueError()
        left, right = value[6:].split('-', 1)
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
        raise S3Error('InvalidRange', 'Requested range is not satisfiable', 416) from None


def list_page(root, directory, prefix, delimiter, after, limit, scan_limit, allowed_prefix):
    """Bounded memory, deterministic pagination. Never silently truncate a scan."""
    fd = open_relative(root, directory, directory=True)
    count = 0

    def walk(parent_fd, base='', depth=0):
        nonlocal count
        if depth > 64:
            raise S3Error('SlowDown', 'Directory depth exceeds listing limit', 503)
        with os.scandir(parent_fd) as entries:
            for item in entries:
                count += 1
                if count > scan_limit:
                    raise S3Error('SlowDown', 'Listing scan limit exceeded; use a narrower prefix or configure a dedicated bucket', 503)
                name = base + item.name
                if item.is_symlink():
                    continue
                if item.is_dir(follow_symlinks=False):
                    # Prune branches outside the requested/authorized prefix.
                    branch = name + '/'
                    if any(p and not (branch.startswith(p) or p.startswith(branch)) for p in (prefix, allowed_prefix)):
                        continue
                    child = None
                    try:
                        child = os.open(item.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
                        yield from walk(child, branch, depth + 1)
                    except FileNotFoundError:
                        continue
                    finally:
                        if child is not None: os.close(child)
                elif item.is_file(follow_symlinks=False) and name.startswith(prefix) and name.startswith(allowed_prefix):
                    # Names with controls / invalid UTF-8 cannot be represented safely in XML.
                    try: parts(name)
                    except (S3Error, UnicodeError): continue
                    suffix = name[len(prefix):]
                    if delimiter and '/' in suffix:
                        key = prefix + suffix.split('/', 1)[0] + '/'
                        if key > after: yield key, None
                    elif name > after:
                        try: yield name, metadata(item.stat(follow_symlinks=False))
                        except FileNotFoundError: continue

    # Keep only a page of unique keys; iterating a large directory doesn't build a full inventory.
    selected = {}
    heap = []
    class ReverseKey(str):
        def __lt__(self, other): return str.__gt__(self, other)
    try:
        for key, info in walk(fd):
            if key in selected: continue
            if len(selected) < limit + 1:
                selected[key] = info; heapq.heappush(heap, ReverseKey(key))
            elif key < heap[0]:
                removed = heapq.heapreplace(heap, ReverseKey(key))
                del selected[str(removed)]; selected[key] = info
        ordered = sorted(selected.items())
        return ordered[:limit], len(ordered) > limit
    finally:
        os.close(fd)
