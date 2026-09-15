from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.core.files.uploadhandler import MemoryFileUploadHandler


class MemoryOnlyUploadHandler(MemoryFileUploadHandler):
    """Keep every upload in memory and refuse oversized requests up front.

    Django's default handler chain spools large files to a temporary file on disk before the
    form gets to reject them. Uploads here hold salary data, so they never touch the disk.
    """

    def handle_raw_input(self, input_data, META, content_length, boundary, encoding=None):
        if content_length > settings.FILE_UPLOAD_MAX_MEMORY_SIZE:
            raise RequestDataTooBig("The upload is larger than the allowed size.")
        self.activated = True
