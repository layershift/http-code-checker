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
    Fixed to handle misaligned screenshots (different heights)
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
        'debug_info': {}  # Added for debugging
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
            result['error'] = f"Previous screenshot file not found: {previous_snapshot.screenshot.path}"
            return result
        if not os.path.exists(current_snapshot.screenshot.path):
            result['error'] = f"Current screenshot file not found: {current_snapshot.screenshot.path}"
            return result
        
        print(f"📸 Loading images for comparison...")
        print(f"  Previous: {previous_snapshot.screenshot.path}")
        print(f"  Current: {current_snapshot.screenshot.path}")
        
        # Open images - synchronous
        prev_img = Image.open(previous_snapshot.screenshot.path)
        curr_img = Image.open(current_snapshot.screenshot.path)
        
        # DEBUG: Print image details
        print(f"  Previous format: {prev_img.format}, mode: {prev_img.mode}, size: {prev_img.size}")
        print(f"  Current format: {curr_img.format}, mode: {curr_img.mode}, size: {curr_img.size}")
        
        result['debug_info']['prev_size'] = prev_img.size
        result['debug_info']['curr_size'] = curr_img.size
        result['debug_info']['prev_mode'] = prev_img.mode
        result['debug_info']['curr_mode'] = curr_img.mode
        
        # Convert both to RGB to ensure consistency
        if prev_img.mode != 'RGB':
            print(f"  Converting previous from {prev_img.mode} to RGB")
            prev_img = prev_img.convert('RGB')
        if curr_img.mode != 'RGB':
            print(f"  Converting current from {curr_img.mode} to RGB")
            curr_img = curr_img.convert('RGB')
        
        # ===== FIX 1: Handle height differences by cropping to minimum height =====
        # This prevents the "doubled letters" effect from different page heights
        min_height = min(prev_img.height, curr_img.height)
        min_width = min(prev_img.width, curr_img.width)
        
        if prev_img.size != (min_width, min_height) or curr_img.size != (min_width, min_height):
            print(f"📏 Cropping both images to common area: {min_width}x{min_height}")
            prev_img = prev_img.crop((0, 0, min_width, min_height))
            curr_img = curr_img.crop((0, 0, min_width, min_height))
            result['debug_info']['cropped_to_common'] = True
        else:
            result['debug_info']['cropped_to_common'] = False
        
        # ===== FIX 2: Detect and correct vertical shifts using template matching =====
        # Convert to numpy arrays for alignment check
        prev_array_temp = np.array(prev_img)
        curr_array_temp = np.array(curr_img)
        
        prev_gray_temp = cv2.cvtColor(prev_array_temp, cv2.COLOR_RGB2GRAY)
        curr_gray_temp = cv2.cvtColor(curr_array_temp, cv2.COLOR_RGB2GRAY)
        
        # Use top portion as template (assuming top of page is most stable)
        template_height = min(100, prev_gray_temp.shape[0] // 4)
        template = prev_gray_temp[:template_height, :]
        
        # Find best match in current image
        try:
            result_match = cv2.matchTemplate(curr_gray_temp, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result_match)
            
            y_shift = max_loc[1]
            print(f"📐 Template matching confidence: {max_val:.3f}, detected shift: {y_shift} pixels")
            result['debug_info']['detected_shift'] = int(y_shift)
            result['debug_info']['match_confidence'] = float(max_val)
            
            # Only adjust if shift is significant and confidence is good
            if y_shift > 5 and max_val > 0.5:
                print(f"  🔄 Realigning images - shifting by {y_shift} pixels")
                
                # Crop both images to aligned content
                new_height = min_height - y_shift
                if new_height > 100:  # Ensure we still have enough image
                    prev_img = prev_img.crop((0, 0, min_width, min_height))
                    curr_img = curr_img.crop((0, y_shift, min_width, y_shift + new_height))
                    print(f"  ✅ Images realigned, new common area: {min_width}x{new_height}")
                    result['debug_info']['realigned'] = True
                else:
                    result['debug_info']['realigned'] = False
            else:
                result['debug_info']['realigned'] = False
                print(f"  ℹ️ No significant shift detected, using original alignment")
                
        except Exception as e:
            print(f"  ⚠️ Template matching failed: {e}, using original alignment")
            result['debug_info']['template_match_error'] = str(e)
        
        # Convert to numpy arrays for processing
        prev_array = np.array(prev_img)
        curr_array = np.array(curr_img)
        
        print(f"  Final array shapes: Previous {prev_array.shape}, Current {curr_array.shape}")
        
        result['debug_info']['final_prev_shape'] = prev_array.shape
        result['debug_info']['final_curr_shape'] = curr_array.shape
        
        # Convert to grayscale
        prev_gray = cv2.cvtColor(prev_array, cv2.COLOR_RGB2GRAY)
        curr_gray = cv2.cvtColor(curr_array, cv2.COLOR_RGB2GRAY)
        
        # DEBUG: Check if images are identical
        if np.array_equal(prev_gray, curr_gray):
            print("✅ Images are IDENTICAL in grayscale")
            result['debug_info']['identical'] = True
        else:
            print("❌ Images are DIFFERENT in grayscale")
            result['debug_info']['identical'] = False
            
            # Calculate simple difference statistics
            abs_diff = np.abs(prev_gray.astype(np.int16) - curr_gray.astype(np.int16))
            print(f"  Mean absolute difference: {np.mean(abs_diff):.2f}")
            print(f"  Max absolute difference: {np.max(abs_diff)}")
            result['debug_info']['mean_diff'] = float(np.mean(abs_diff))
            result['debug_info']['max_diff'] = int(np.max(abs_diff))
        
        # Calculate SSIM with proper parameters
        print("📊 Calculating SSIM...")
        
        # Ensure images are in the correct range (0-255 for uint8)
        if prev_gray.dtype != np.uint8:
            prev_gray = prev_gray.astype(np.uint8)
        if curr_gray.dtype != np.uint8:
            curr_gray = curr_gray.astype(np.uint8)
        
        # Calculate SSIM with appropriate window size
        # For small images, use a smaller window
        min_dim = min(prev_gray.shape)
        win_size = min(7, min_dim)
        if win_size % 2 == 0:  # win_size must be odd
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
        except Exception as e:
            print(f"  SSIM calculation failed: {e}")
            result['error'] = f"SSIM calculation failed: {e}"
            # Fallback to MSE
            mse = np.mean((prev_gray.astype(float) - curr_gray.astype(float)) ** 2)
            if mse == 0:
                result['ssim_score'] = 1.0
            else:
                # Approximate conversion
                result['ssim_score'] = 1.0 / (1.0 + mse/1000)
            print(f"  Using MSE fallback: MSE={mse:.2f}, SSIM≈{result['ssim_score']:.4f}")
        
        # Calculate pixel differences with THRESHOLD to ignore minor variations
        pixel_diff = np.abs(prev_gray.astype(np.int16) - curr_gray.astype(np.int16))
        
        # Use adaptive threshold based on image characteristics
        threshold = 15  # Ignore tiny differences (anti-aliasing, compression artifacts)
        changed_pixels = np.sum(pixel_diff > threshold)
        total_pixels = pixel_diff.size
        
        result['changed_pixels'] = int(changed_pixels)
        result['total_pixels'] = total_pixels
        result['percent_difference'] = float((changed_pixels / total_pixels) * 100)
        
        print(f"  Changed pixels (threshold={threshold}): {changed_pixels}/{total_pixels} ({result['percent_difference']:.2f}%)")
        
        # Calculate mean brightness difference
        prev_mean = np.mean(prev_gray)
        curr_mean = np.mean(curr_gray)
        result['mean_brightness_diff'] = float(abs(prev_mean - curr_mean))
        print(f"  Brightness difference: {result['mean_brightness_diff']:.2f}")
        
        # GENERATE AND SAVE VISUALIZATIONS if output_dir is provided
        if output_dir and result['ssim_score'] is not None:
            print(f"📁 Generating visualization images in: {output_dir}")
            os.makedirs(output_dir, exist_ok=True)
            
            # Create difference map (for heatmap)
            if 'diff' in locals():
                diff_uint8 = (diff * 255).astype("uint8")
            else:
                # Fallback if diff not available
                diff_uint8 = pixel_diff.astype("uint8")
                # Normalize to 0-255
                if diff_uint8.max() > 0:
                    diff_uint8 = (diff_uint8 / diff_uint8.max() * 255).astype("uint8")
            
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
            
            # Mark changed pixels in red (using same threshold as before)
            diff_mask = (pixel_diff > threshold).astype(np.uint8) * 255
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