# monitoring/comparison.py - Ensure all functions are synchronous
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from PIL import Image
import os
import logging

logger = logging.getLogger(__name__)

def compare_screenshots(previous_snapshot, current_snapshot, output_dir=None):
    """
    Synchronous comparison function - no async anywhere
    """
    result = {
        'ssim_score': None,
        'percent_difference': None,
        'changed_pixels': None,
        'total_pixels': None,
        'histogram_similarity': None,
        'mean_brightness_diff': None,
        'diff_image_path': None,
        'heatmap_image_path': None,
        'error': None
    }
    
    try:
        # Check if both snapshots have screenshots
        if not previous_snapshot.screenshot:
            result['error'] = "Previous snapshot has no screenshot"
            return result
        if not current_snapshot.screenshot:
            result['error'] = "Current snapshot has no screenshot"
            return result
        
        # Check if files exist
        if not os.path.exists(previous_snapshot.screenshot.path):
            result['error'] = f"Previous screenshot file not found"
            return result
        if not os.path.exists(current_snapshot.screenshot.path):
            result['error'] = f"Current screenshot file not found"
            return result
        
        # Open images - synchronous
        prev_img = Image.open(previous_snapshot.screenshot.path)
        curr_img = Image.open(current_snapshot.screenshot.path)
        
        # Resize if needed
        if prev_img.size != curr_img.size:
            curr_img = curr_img.resize(prev_img.size)
        
        # Convert to arrays
        prev_array = np.array(prev_img.convert('RGB'))
        curr_array = np.array(curr_img.convert('RGB'))
        
        # Convert to grayscale
        prev_gray = cv2.cvtColor(prev_array, cv2.COLOR_RGB2GRAY)
        curr_gray = cv2.cvtColor(curr_array, cv2.COLOR_RGB2GRAY)
        
        # Calculate SSIM
        ssim_score, diff = ssim(prev_gray, curr_gray, full=True, data_range=255)
        result['ssim_score'] = float(ssim_score)
        
        # Calculate pixel differences
        pixel_diff = np.abs(prev_gray.astype(int) - curr_gray.astype(int))
        changed_pixels = np.sum(pixel_diff > 0)
        total_pixels = pixel_diff.size
        
        result['changed_pixels'] = int(changed_pixels)
        result['total_pixels'] = total_pixels
        result['percent_difference'] = float((changed_pixels / total_pixels) * 100)
        
        return result
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error comparing screenshots: {e}")
        return result
