import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


@dataclass
class Config:
    public_url: str
    admin_key: str
    roots: dict[str, str]
    state_path: str = '/state/gateway.sqlite3'
    region: str = 'us-east-1'
    max_ttl: int = 86400
    scan_limit: int = 100000
    root_paths: dict[str, str] = field(init=False)

    def __post_init__(self):
        self.public_url = self.public_url.rstrip('/')
        url = urlsplit(self.public_url)
        if url.scheme not in {'http', 'https'} or not url.netloc or url.query or url.fragment or url.username:
            raise ValueError('FS3_PUBLIC_URL must be an absolute HTTP(S) endpoint')
        if len(self.admin_key) < 32:
            raise ValueError('FS3_ADMIN_KEY must contain at least 32 characters')
        if not self.roots:
            raise ValueError('Configure at least one FS3_ROOTS directory')
        self.root_paths = {}
        for name, value in self.roots.items():
            if not name or not isinstance(value, str) or not Path(value).is_absolute():
                raise ValueError('FS3_ROOTS must map names to absolute directories')
            path = Path(value).resolve(strict=True)
            if not path.is_dir():
                raise ValueError('Every configured root must be a directory')
            self.root_paths[name] = str(path)

    @classmethod
    def from_env(cls):
        return cls(public_url=os.environ['FS3_PUBLIC_URL'], admin_key=os.environ['FS3_ADMIN_KEY'],
                   roots=json.loads(os.environ['FS3_ROOTS']), state_path=os.getenv('FS3_STATE_PATH', '/state/gateway.sqlite3'),
                   region=os.getenv('FS3_REGION', 'us-east-1'), scan_limit=int(os.getenv('FS3_SCAN_LIMIT', '100000')))
