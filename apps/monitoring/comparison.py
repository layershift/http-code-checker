# monitoring/comparison.py - Complete with heatmap and difference image generation
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from PIL import Image
import os
import logging

logger = logging.getLogger(__name__)

def compare_screenshots(previous_snapshot, current_snapshot, output_dir=None):
    """
    Synchronous comparison function - generates heatmaps and difference images
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
        
        print(f"📸 Loading images for comparison...")
        print(f"  Previous: {previous_snapshot.screenshot.path}")
        print(f"  Current: {current_snapshot.screenshot.path}")
        
        # Open images - synchronous
        prev_img = Image.open(previous_snapshot.screenshot.path)
        curr_img = Image.open(current_snapshot.screenshot.path)
        
        print(f"  Previous size: {prev_img.size}")
        print(f"  Current size: {curr_img.size}")
        
        # Resize if needed
        if prev_img.size != curr_img.size:
            print(f"🔄 Resizing current image to match previous: {prev_img.size}")
            curr_img = curr_img.resize(prev_img.size)
        
        # Convert to arrays
        prev_array = np.array(prev_img.convert('RGB'))
        curr_array = np.array(curr_img.convert('RGB'))
        
        print(f"  Array shapes: Previous {prev_array.shape}, Current {curr_array.shape}")
        
        # Convert to grayscale
        prev_gray = cv2.cvtColor(prev_array, cv2.COLOR_RGB2GRAY)
        curr_gray = cv2.cvtColor(curr_array, cv2.COLOR_RGB2GRAY)
        
        # Calculate SSIM
        print("📊 Calculating SSIM...")
        ssim_score, diff = ssim(prev_gray, curr_gray, full=True, data_range=255)
        result['ssim_score'] = float(ssim_score)
        print(f"  SSIM Score: {ssim_score:.4f}")
        
        # Calculate pixel differences
        pixel_diff = np.abs(prev_gray.astype(int) - curr_gray.astype(int))
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
        
        # GENERATE AND SAVE VISUALIZATIONS if output_dir is provided
        if output_dir:
            print(f"📁 Generating visualization images in: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
            
            # Create difference map (for heatmap)
            diff_uint8 = (diff * 255).astype("uint8")
            
            # ===== 1. CREATE HEATMAP =====
            print("  🔥 Creating heatmap...")
            heatmap = cv2.applyColorMap(diff_uint8, cv2.COLORMAP_JET)
            heatmap_path = os.path.join(
                output_dir, 
                f"heatmap_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(heatmap_path, heatmap)
            result['heatmap_image_path'] = heatmap_path
            print(f"  ✅ Heatmap saved: {heatmap_path}")
            
            # ===== 2. CREATE DIFFERENCE OVERLAY (red for changes) =====
            print("  🔴 Creating difference overlay...")
            diff_overlay = prev_array.copy()
            
            # Mark changed pixels in red (where difference > 30)
            diff_threshold = 30
            diff_mask = cv2.threshold(diff_uint8, diff_threshold, 255, cv2.THRESH_BINARY)[1]
            diff_overlay[diff_mask > 0] = [255, 0, 0]  # Red
            
            diff_path = os.path.join(
                output_dir,
                f"diff_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(diff_path, cv2.cvtColor(diff_overlay, cv2.COLOR_RGB2BGR))
            result['diff_image_path'] = diff_path
            print(f"  ✅ Difference overlay saved: {diff_path}")
            
            # ===== 3. CREATE SIDE-BY-SIDE COMPARISON =====
            print("  🖼️ Creating side-by-side comparison...")
            h, w = prev_gray.shape
            comparison = np.zeros((h, w*2, 3), dtype=np.uint8)
            comparison[:, :w] = prev_array
            comparison[:, w:] = curr_array
            
            # Add a red line between them
            comparison[:, w-2:w+2] = [255, 0, 0]
            
            side_by_side_path = os.path.join(
                output_dir,
                f"side_by_side_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(side_by_side_path, cv2.cvtColor(comparison, cv2.COLOR_RGB2BGR))
            result['side_by_side_path'] = side_by_side_path
            print(f"  ✅ Side-by-side saved: {side_by_side_path}")
            
            # ===== 4. CREATE DIFFERENCE INTENSITY MAP =====
            print("  🌡️ Creating difference intensity map...")
            # Normalize pixel differences for visualization
            if pixel_diff.max() > 0:
                norm_diff = (pixel_diff / pixel_diff.max() * 255).astype(np.uint8)
            else:
                norm_diff = np.zeros_like(pixel_diff, dtype=np.uint8)
            
            intensity_map = cv2.applyColorMap(norm_diff, cv2.COLORMAP_HOT)
            intensity_path = os.path.join(
                output_dir,
                f"intensity_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(intensity_path, intensity_map)
            print(f"  ✅ Intensity map saved: {intensity_path}")
            
            # ===== 5. CREATE BLENDED OVERLAY (semi-transparent) =====
            print("  🎨 Creating blended overlay...")
            # Create a red overlay for changes
            red_overlay = np.zeros_like(prev_array)
            red_overlay[diff_mask > 0] = [255, 0, 0]
            
            # Blend with original
            alpha = 0.5
            blended = cv2.addWeighted(prev_array, 1 - alpha, red_overlay, alpha, 0)
            
            blended_path = os.path.join(
                output_dir,
                f"blended_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(blended_path, cv2.cvtColor(blended, cv2.COLOR_RGB2BGR))
            print(f"  ✅ Blended overlay saved: {blended_path}")
        
        # Calculate histogram similarity (optional)
        try:
            prev_hist = cv2.calcHist([prev_array], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            curr_hist = cv2.calcHist([curr_array], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            
            prev_hist = cv2.normalize(prev_hist, prev_hist).flatten()
            curr_hist = cv2.normalize(curr_hist, curr_hist).flatten()
            
            hist_similarity = cv2.compareHist(prev_hist, curr_hist, cv2.HISTCMP_CORREL)
            result['histogram_similarity'] = float(hist_similarity)
            print(f"  Histogram similarity: {hist_similarity:.4f}")
        except Exception as e:
            print(f"  Histogram calculation failed: {e}")
        
        return result
        
    except Exception as e:
        result['error'] = str(e)
        logger.error(f"Error comparing screenshots: {e}")
        import traceback
        traceback.print_exc()
        return result