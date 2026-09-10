class S3Error(Exception):
    def __init__(self, code='AccessDenied', message='Access denied', status=403):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)
