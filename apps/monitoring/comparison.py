# monitoring/comparison.py
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from PIL import Image
import os
import logging
import requests
from io import BytesIO

logger = logging.getLogger(__name__)

def get_image_from_snapshot(snapshot):
    """
    Get PIL Image from a snapshot, handling both local and remote storage
    """
    try:
        url = snapshot.screenshot.url
        print(f"📥 Downloading image from URL: {url}")
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return Image.open(BytesIO(response.content))
    except Exception as e:
        print(f"⚠️ Failed to download via URL: {e}")
    
    # Fallback: try local path
    try:
        if hasattr(snapshot.screenshot, 'path'):
            try:
                path = snapshot.screenshot.path
                if os.path.exists(path):
                    print(f"📁 Loading image from local path: {path}")
                    return Image.open(path)
            except NotImplementedError:
                print(f"⚠️ Storage doesn't support .path()")
    except Exception as e:
        print(f"⚠️ Failed to load from local path: {e}")
    
    raise Exception(f"Could not load image for snapshot {snapshot.id}")

def compare_screenshots(previous_snapshot, current_snapshot, output_dir=None):
    """
    Synchronous comparison function - generates heatmaps and difference images
    Works with both local and remote storage
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
        'side_by_side_path': None,
        'error': None,
        'debug_info': {}
    }
    
    try:
        # Check if both snapshots have screenshots
        if not previous_snapshot.screenshot:
            result['error'] = "Previous snapshot has no screenshot"
            return result
        if not current_snapshot.screenshot:
            result['error'] = "Current snapshot has no screenshot"
            return result
        
        print(f"📸 Loading images for comparison...")
        
        # Get images using the helper function
        try:
            prev_img = get_image_from_snapshot(previous_snapshot)
            curr_img = get_image_from_snapshot(current_snapshot)
            print(f"✅ Images loaded successfully")
        except Exception as e:
            result['error'] = f"Failed to load images: {e}"
            print(f"❌ {result['error']}")
            return result
        
        print(f"  Previous: {prev_img.size}, mode: {prev_img.mode}")
        print(f"  Current: {curr_img.size}, mode: {curr_img.mode}")
        
        # Convert both to RGB
        if prev_img.mode != 'RGB':
            print(f"  Converting previous from {prev_img.mode} to RGB")
            prev_img = prev_img.convert('RGB')
        if curr_img.mode != 'RGB':
            print(f"  Converting current from {curr_img.mode} to RGB")
            curr_img = curr_img.convert('RGB')
        
        # Resize if needed
        if prev_img.size != curr_img.size:
            print(f"🔄 Resizing current image to match previous: {prev_img.size}")
            curr_img = curr_img.resize(prev_img.size, Image.Resampling.LANCZOS)
        
        # Convert to numpy arrays
        prev_array = np.array(prev_img)
        curr_array = np.array(curr_img)
        
        print(f"  Array shapes: Previous {prev_array.shape}, Current {curr_array.shape}")
        
        # Convert to grayscale
        prev_gray = cv2.cvtColor(prev_array, cv2.COLOR_RGB2GRAY)
        curr_gray = cv2.cvtColor(curr_array, cv2.COLOR_RGB2GRAY)
        
        # Check if images are identical
        if np.array_equal(prev_gray, curr_gray):
            print("✅ Images are IDENTICAL in grayscale")
            result['ssim_score'] = 1.0
            result['percent_difference'] = 0.0
            result['changed_pixels'] = 0
            result['total_pixels'] = prev_gray.size
            # Even if identical, we can still create visualizations
            # Create a blank diff (all black) for identical images
            diff = np.zeros_like(prev_gray, dtype=np.float64)
            diff_uint8 = np.zeros_like(prev_gray, dtype=np.uint8)
        else:
            # Calculate SSIM
            print("📊 Calculating SSIM...")
            
            min_dim = min(prev_gray.shape)
            win_size = min(7, min_dim)
            if win_size % 2 == 0:
                win_size -= 1
            if win_size < 3:
                win_size = 3
            
            print(f"  Using win_size={win_size} for image of size {prev_gray.shape}")
            
            try:
                ssim_score, diff = ssim(
                    prev_gray, 
                    curr_gray, 
                    full=True, 
                    data_range=255,
                    win_size=win_size
                )
                result['ssim_score'] = float(ssim_score)
                print(f"  SSIM Score: {ssim_score:.6f}")
                diff_uint8 = (diff * 255).astype("uint8")
            except Exception as e:
                print(f"  SSIM calculation failed: {e}")
                result['error'] = f"SSIM calculation failed: {e}"
                mse = np.mean((prev_gray.astype(float) - curr_gray.astype(float)) ** 2)
                if mse == 0:
                    result['ssim_score'] = 1.0
                else:
                    result['ssim_score'] = 1.0 / (1.0 + mse/1000)
                print(f"  Using MSE fallback: MSE={mse:.2f}, SSIM≈{result['ssim_score']:.4f}")
                # Create diff from pixel difference
                pixel_diff = np.abs(prev_gray.astype(np.int16) - curr_gray.astype(np.int16))
                if pixel_diff.max() > 0:
                    diff_uint8 = (pixel_diff / pixel_diff.max() * 255).astype(np.uint8)
                else:
                    diff_uint8 = np.zeros_like(prev_gray, dtype=np.uint8)
        
        # Calculate pixel differences
        pixel_diff = np.abs(prev_gray.astype(np.int16) - curr_gray.astype(np.int16))
        changed_pixels = np.sum(pixel_diff > 0)
        total_pixels = pixel_diff.size
        
        result['changed_pixels'] = int(changed_pixels)
        result['total_pixels'] = total_pixels
        result['percent_difference'] = float((changed_pixels / total_pixels) * 100)
        
        print(f"  Changed pixels: {changed_pixels}/{total_pixels} ({result['percent_difference']:.2f}%)")
        
        # Calculate mean brightness difference
        prev_mean = np.mean(prev_gray)
        curr_mean = np.mean(curr_gray)
        result['mean_brightness_diff'] = float(abs(prev_mean - curr_mean))
        print(f"  Brightness difference: {result['mean_brightness_diff']:.2f}")
        
        # ===== GENERATE AND SAVE VISUALIZATIONS =====
        print(f"📁 output_dir = {output_dir}")
        
        if output_dir:
            print(f"📁 Generating visualization images in: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
            
            # Create heatmap
            print("  🔥 Creating heatmap...")
            heatmap = cv2.applyColorMap(diff_uint8, cv2.COLORMAP_JET)
            heatmap_path = os.path.join(output_dir, f"heatmap_{previous_snapshot.id}_vs_{current_snapshot.id}.png")
            cv2.imwrite(heatmap_path, heatmap)
            result['heatmap_image_path'] = heatmap_path
            print(f"  ✅ Heatmap saved: {heatmap_path}")
            
            # Create difference overlay
            print("  🔴 Creating difference overlay...")
            diff_overlay = prev_array.copy()
            diff_threshold = 30
            diff_mask = cv2.threshold(diff_uint8, diff_threshold, 255, cv2.THRESH_BINARY)[1]
            diff_overlay[diff_mask > 0] = [255, 0, 0]  # Red for changes
            
            diff_path = os.path.join(output_dir, f"diff_{previous_snapshot.id}_vs_{current_snapshot.id}.png")
            cv2.imwrite(diff_path, cv2.cvtColor(diff_overlay, cv2.COLOR_RGB2BGR))
            result['diff_image_path'] = diff_path
            print(f"  ✅ Difference overlay saved: {diff_path}")
            
            # Create side-by-side comparison
            print("  🖼️ Creating side-by-side comparison...")
            h, w = prev_gray.shape
            comparison = np.zeros((h, w*2, 3), dtype=np.uint8)
            comparison[:, :w] = prev_array
            comparison[:, w:] = curr_array
            comparison[:, w-2:w+2] = [255, 0, 0]
            
            side_by_side_path = os.path.join(output_dir, f"side_by_side_{previous_snapshot.id}_vs_{current_snapshot.id}.png")
            cv2.imwrite(side_by_side_path, cv2.cvtColor(comparison, cv2.COLOR_RGB2BGR))
            result['side_by_side_path'] = side_by_side_path
            print(f"  ✅ Side-by-side saved: {side_by_side_path}")
        else:
            print(f"⚠️ No output_dir provided, skipping visualization generation")
        
        print(f"✅ Comparison complete. heatmap_path: {result.get('heatmap_image_path')}, diff_path: {result.get('diff_image_path')}")
        return result
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error comparing screenshots: {e}")
        import traceback
        traceback.print_exc()
        return result