# monitoring/comparison.py
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from django.core.files.base import ContentFile
from django.utils import timezone
import tempfile
import os
from PIL import Image

def compare_screenshots(previous_snapshot, current_snapshot, output_dir=None):
    """
    Compare two screenshots and return metrics and difference images
    
    Args:
        previous_snapshot: First SiteSnapshot object
        current_snapshot: Second SiteSnapshot object
        output_dir: Optional directory to save outputs
    
    Returns:
        dict: Comparison results
    """
    result = {
        'ssim_score': None,
        'percent_difference': None,
        'changed_pixels': None,
        'total_pixels': None,
        'diff_image_path': None,
        'heatmap_image_path': None
    }
    
    try:
        # Check if both snapshots have screenshots
        if not previous_snapshot.screenshot or not current_snapshot.screenshot:
            print("Both snapshots must have screenshots")
            return result
        
        # Open images
        prev_img = Image.open(previous_snapshot.screenshot.path)
        curr_img = Image.open(current_snapshot.screenshot.path)
        
        # Convert to same size if needed
        if prev_img.size != curr_img.size:
            curr_img = curr_img.resize(prev_img.size)
        
        # Convert to numpy arrays
        prev_array = np.array(prev_img.convert('RGB'))
        curr_array = np.array(curr_img.convert('RGB'))
        
        # Convert to grayscale for SSIM
        prev_gray = cv2.cvtColor(prev_array, cv2.COLOR_RGB2GRAY)
        curr_gray = cv2.cvtColor(curr_array, cv2.COLOR_RGB2GRAY)
        
        # Calculate SSIM
        ssim_score, diff = ssim(prev_gray, curr_gray, full=True, data_range=255)
        result['ssim_score'] = float(ssim_score)
        
        # Create difference mask
        diff = (diff * 255).astype("uint8")
        
        # Calculate changed pixels
        threshold = 0.05  # 5% difference threshold
        diff_mask = cv2.threshold(diff, int(threshold * 255), 255, cv2.THRESH_BINARY)[1]
        
        # Count changed pixels
        result['changed_pixels'] = int(np.sum(diff_mask > 0))
        result['total_pixels'] = diff_mask.size
        result['percent_difference'] = float((result['changed_pixels'] / result['total_pixels']) * 100)
        
        # Create heatmap
        heatmap = cv2.applyColorMap(diff, cv2.COLORMAP_JET)
        
        # Create difference overlay
        diff_overlay = prev_array.copy()
        diff_overlay[diff_mask > 0] = [0, 0, 255]  # Mark changed pixels in red
        
        # Save images if output_dir is provided
        if output_dir:
            os.makedirs(output_dir, exist_ok=True)
            
            # Save heatmap
            heatmap_path = os.path.join(
                output_dir, 
                f"heatmap_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(heatmap_path, heatmap)
            result['heatmap_image_path'] = heatmap_path
            
            # Save diff overlay
            diff_path = os.path.join(
                output_dir,
                f"diff_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(diff_path, cv2.cvtColor(diff_overlay, cv2.COLOR_RGB2BGR))
            result['diff_image_path'] = diff_path
            
            # Save difference mask
            mask_path = os.path.join(
                output_dir,
                f"mask_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(mask_path, diff_mask)
            result['mask_image_path'] = mask_path
        
        # Print results
        print(f"SSIM Score: {result['ssim_score']:.4f}")
        print(f"Difference Percentage: {result['percent_difference']:.2f}%")
        print(f"Changed Pixels: {result['changed_pixels']}")
        print(f"Total Pixels: {result['total_pixels']}")
        if result.get('heatmap_image_path'):
            print(f"Heatmap Saved As: {result['heatmap_image_path']}")
        
        return result
        
    except Exception as e:
        print(f"Error comparing screenshots: {e}")
        return result
