# monitoring/storage.py
import requests
from django.core.files.storage import Storage
from django.core.files.base import ContentFile
from django.utils.deconstruct import deconstructible
from django.conf import settings
import logging

logger = logging.getLogger(__name__)

@deconstructible
class RemoteUploaderStorage(Storage):
    """
    Custom storage class that uploads files to a remote uploader service
    """
    
    def __init__(self, base_url=None):
        self.base_url = base_url or getattr(settings, 'REMOTE_UPLOADER_URL', 'http://dont-delete-uploader.man-1.solus.stage.town:8000')
        self.upload_endpoint = f"{self.base_url}/upload"
        # FIXED: Use /image/ instead of /download/
        self.download_endpoint = f"{self.base_url}/image"
        print(f"🔧 RemoteUploaderStorage initialized")
        print(f"   Upload URL: {self.upload_endpoint}")
        print(f"   Download URL: {self.download_endpoint}")
        
    def _save(self, name, content):
        """
        Save a file to the remote uploader
        """
        try:
            # Reset file position
            if hasattr(content, 'seek'):
                content.seek(0)
            
            # Create multipart form data
            files = {'file': (name, content, 'image/png')}
            
            print(f"📤 Uploading to: {self.upload_endpoint}")
            
            # Upload to remote service
            response = requests.post(
                self.upload_endpoint,
                files=files,
                timeout=30
            )
            
            print(f"📤 Upload end")

            if response.status_code == 200:
                data = response.json()
                file_id = data.get('file_id')
                print(f"✅ File uploaded successfully: {name} -> file_id: {file_id}")
                return file_id
            else:
                logger.error(f"Upload failed: {response.status_code} - {response.text}")
                raise Exception(f"Upload failed: {response.text}")
                
        except Exception as e:
            logger.error(f"Error saving file to remote storage: {e}")
            raise
    
    def url(self, name):
        """
        Get the URL for a file
        """
        # FIXED: Use /image/ endpoint
        url = f"{self.download_endpoint}/{name}"
        print(f"🔗 Generated URL: {url}")
        return url
    
    def exists(self, name):
        """
        Check if a file exists
        """
        try:
            response = requests.head(
                f"{self.download_endpoint}/{name}",
                timeout=10
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def delete(self, name):
        """
        Delete a file from remote storage
        """
        try:
            response = requests.delete(
                f"{self.delete_endpoint}/{name}",
                timeout=30
            )
            return response.status_code == 200
        except Exception:
            return False
    
    def size(self, name):
        """
        Get file size
        """
        try:
            response = requests.head(
                f"{self.download_endpoint}/{name}",
                timeout=10
            )
            if response.status_code == 200:
                return int(response.headers.get('content-length', 0))
        except Exception:
            pass
        return 0
    
    def open(self, name, mode='rb'):
        """
        Open a file from remote storage
        """
        try:
            response = requests.get(
                f"{self.download_endpoint}/{name}",
                timeout=30,
                stream=True
            )
            
            if response.status_code == 200:
                return ContentFile(response.content, name=name)
        except Exception as e:
            logger.error(f"Error opening file {name}: {e}")
        
        raise FileNotFoundError(f"File {name} not found")
    
    def get_available_name(self, name, max_length=None):
        """
        Return a filename that's free on the target storage system.
        """
        return name
    
    def generate_filename(self, filename):
        """
        Generate a filename for the uploaded file
        """
        return filename