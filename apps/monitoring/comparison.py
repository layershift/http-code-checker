# monitoring/comparison.py
import cv2
import numpy as np
from skimage.metrics import structural_similarity as ssim
from PIL import Image
import os
import logging
import hashlib

logger = logging.getLogger(__name__)

def debug_image_properties(image_path, label):
    """Print detailed debug info about an image"""
    try:
        print(f"\n🔍 DEBUG - {label}:")
        print(f"  Path: {image_path}")
        
        # File stats
        file_size = os.path.getsize(image_path)
        print(f"  File size: {file_size} bytes")
        
        # Open image
        img = Image.open(image_path)
        print(f"  Format: {img.format}")
        print(f"  Size: {img.size}")
        print(f"  Mode: {img.mode}")
        
        # Convert to RGB for analysis
        img_rgb = img.convert('RGB')
        img_array = np.array(img_rgb)
        
        # Basic stats
        print(f"  Array shape: {img_array.shape}")
        print(f"  Data type: {img_array.dtype}")
        print(f"  Mean pixel value (R,G,B): {np.mean(img_array, axis=(0,1))}")
        print(f"  Std deviation (R,G,B): {np.std(img_array, axis=(0,1))}")
        print(f"  Min value: {np.min(img_array)}")
        print(f"  Max value: {np.max(img_array)}")
        
        # First few pixels
        pixels = list(img_rgb.getdata())[:3]
        print(f"  First 3 pixels (RGB): {pixels}")
        
        # Last few pixels
        width, height = img.size
        last_pixels = [img_rgb.getpixel((width-1, 0)) for _ in range(3)]
        print(f"  Last 3 pixels: {last_pixels}")
        
        # Image hash
        img_bytes = img_array.tobytes()
        img_hash = hashlib.md5(img_bytes).hexdigest()
        print(f"  MD5 hash: {img_hash}")
        
        # Check if image is uniform (all same color)
        unique_colors = len(np.unique(img_array.reshape(-1, 3), axis=0))
        print(f"  Unique colors: {unique_colors}")
        
        if unique_colors == 1:
            print(f"  ⚠️ Image is a single solid color!")
            
        return img_array, img_hash
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None, None

def compare_screenshots(previous_snapshot, current_snapshot, output_dir=None, debug=True):
    """
    Compare two screenshots with multiple metrics to catch edge cases
    """
    result = {
        'ssim_score': None,
        'percent_difference': None,
        'changed_pixels': None,
        'total_pixels': None,
        'histogram_similarity': None,
        'mean_brightness_diff': None,
        'mse': None,  # Mean Squared Error
        'psnr': None,  # Peak Signal-to-Noise Ratio
        'diff_image_path': None,
        'heatmap_image_path': None,
        'previous_hash': None,
        'current_hash': None,
        'error': None,
        'warning': None
    }
    
    print("\n" + "="*70)
    print("🔬 IMAGE COMPARISON DEBUG - START")
    print("="*70)
    
    try:
        # Check if both snapshots have screenshots
        if not previous_snapshot.screenshot:
            result['error'] = "Previous snapshot has no screenshot"
            print(f"❌ {result['error']}")
            return result
            
        if not current_snapshot.screenshot:
            result['error'] = "Current snapshot has no screenshot"
            print(f"❌ {result['error']}")
            return result
        
        # Debug image properties
        if debug:
            prev_array, prev_hash = debug_image_properties(previous_snapshot.screenshot.path, "PREVIOUS")
            curr_array, curr_hash = debug_image_properties(current_snapshot.screenshot.path, "CURRENT")
            result['previous_hash'] = prev_hash
            result['current_hash'] = curr_hash
            
            if prev_hash == curr_hash:
                print("\n✅ IMAGES HAVE IDENTICAL HASHES - They are the exact same file!")
            else:
                print("\n❌ IMAGES HAVE DIFFERENT HASHES - They are different files")
        
        # Open images
        try:
            prev_img = Image.open(previous_snapshot.screenshot.path)
            curr_img = Image.open(current_snapshot.screenshot.path)
            print(f"\n✅ Images opened successfully")
        except Exception as e:
            result['error'] = f"Error opening images: {e}"
            print(f"❌ {result['error']}")
            return result
        
        # Get image sizes
        prev_size = prev_img.size
        curr_size = curr_img.size
        print(f"\n📏 Image sizes: Previous={prev_size}, Current={curr_size}")
        
        # Check if images are valid (not too small)
        if prev_size[0] < 50 or prev_size[1] < 50:
            result['warning'] = f"Previous image too small: {prev_size}"
            print(f"⚠️ {result['warning']}")
            
        if curr_size[0] < 50 or curr_size[1] < 50:
            result['warning'] = f"Current image too small: {curr_size}"
            print(f"⚠️ {result['warning']}")
        
        # Convert to same size if needed
        if prev_size != curr_size:
            print(f"🔄 Resizing current image from {curr_size} to {prev_size}")
            curr_img = curr_img.resize(prev_size)
            result['warning'] = f"Images resized from {curr_size} to {prev_size}"
        
        # Convert to numpy arrays
        prev_array = np.array(prev_img.convert('RGB'))
        curr_array = np.array(curr_img.convert('RGB'))
        print(f"\n📊 Array shapes: Previous={prev_array.shape}, Current={curr_array.shape}")
        
        # Check if images are valid
        if prev_array.size == 0 or curr_array.size == 0:
            result['error'] = "Empty image array"
            print(f"❌ {result['error']}")
            return result
        
        # 1. Calculate histogram similarity
        try:
            print("\n📊 Calculating histogram similarity...")
            prev_hist = cv2.calcHist([prev_array], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            curr_hist = cv2.calcHist([curr_array], [0, 1, 2], None, [8, 8, 8], [0, 256, 0, 256, 0, 256])
            
            # Normalize histograms
            prev_hist = cv2.normalize(prev_hist, prev_hist).flatten()
            curr_hist = cv2.normalize(curr_hist, curr_hist).flatten()
            
            # Calculate histogram correlation
            hist_similarity = cv2.compareHist(prev_hist, curr_hist, cv2.HISTCMP_CORREL)
            result['histogram_similarity'] = float(hist_similarity)
            print(f"  Histogram similarity: {hist_similarity:.6f}")
        except Exception as e:
            result['warning'] = f"Histogram calculation failed: {e}"
            print(f"⚠️ {result['warning']}")
            result['histogram_similarity'] = 0.0
        
        # 2. Convert to grayscale
        try:
            prev_gray = cv2.cvtColor(prev_array, cv2.COLOR_RGB2GRAY)
            curr_gray = cv2.cvtColor(curr_array, cv2.COLOR_RGB2GRAY)
            print(f"\n📊 Grayscale shapes: {prev_gray.shape}")
        except Exception as e:
            result['error'] = f"Grayscale conversion failed: {e}"
            print(f"❌ {result['error']}")
            return result
        
        # 3. Calculate mean brightness
        prev_mean = np.mean(prev_gray)
        curr_mean = np.mean(curr_gray)
        result['mean_brightness_diff'] = float(abs(prev_mean - curr_mean))
        print(f"\n💡 Brightness: Previous={prev_mean:.2f}, Current={curr_mean:.2f}, Diff={result['mean_brightness_diff']:.2f}")
        
        # 4. Check if images are identical (fast check)
        if np.array_equal(prev_gray, curr_gray):
            print("\n✅ IMAGES ARE IDENTICAL (pixel-perfect match)")
            result['ssim_score'] = 1.0
            result['percent_difference'] = 0.0
            result['changed_pixels'] = 0
            result['total_pixels'] = prev_gray.size
            result['mse'] = 0.0
            result['psnr'] = float('inf')
            
            # Still create visualizations if requested
            if output_dir:
                create_visualizations(prev_array, curr_gray, curr_gray, result, previous_snapshot, current_snapshot, output_dir)
            
            print("="*70)
            print("🔬 IMAGE COMPARISON DEBUG - END")
            print("="*70 + "\n")
            return result
        
        # 5. Calculate MSE and PSNR
        mse = np.mean((prev_gray.astype(float) - curr_gray.astype(float)) ** 2)
        result['mse'] = float(mse)
        if mse > 0:
            result['psnr'] = float(20 * np.log10(255.0 / np.sqrt(mse)))
        else:
            result['psnr'] = float('inf')
        print(f"\n📈 MSE: {mse:.2f}, PSNR: {result['psnr']:.2f}dB")
        
        # 6. Calculate SSIM
        try:
            win_size = min(7, prev_gray.shape[0] // 2, prev_gray.shape[1] // 2)
            if win_size < 3:
                win_size = 3
            if win_size % 2 == 0:
                win_size += 1
            print(f"  Using SSIM window size: {win_size}")
            
            ssim_score, diff = ssim(prev_gray, curr_gray, full=True, data_range=255, win_size=win_size)
            result['ssim_score'] = float(ssim_score)
            print(f"  SSIM score: {ssim_score:.6f}")
        except Exception as e:
            result['error'] = f"SSIM calculation failed: {e}"
            print(f"❌ {result['error']}")
            return result
        
        # 7. Calculate pixel differences
        pixel_diff = np.abs(prev_gray.astype(int) - curr_gray.astype(int))
        changed_pixels = np.sum(pixel_diff > 0)
        total_pixels = pixel_diff.size
        
        result['changed_pixels'] = int(changed_pixels)
        result['total_pixels'] = total_pixels
        result['percent_difference'] = float((changed_pixels / total_pixels) * 100)
        
        print(f"\n🔢 Pixel difference analysis:")
        print(f"  Total pixels: {total_pixels}")
        print(f"  Changed pixels: {changed_pixels}")
        print(f"  Percent different: {result['percent_difference']:.4f}%")
        print(f"  Mean pixel difference: {np.mean(pixel_diff):.2f}")
        print(f"  Max pixel difference: {np.max(pixel_diff)}")
        print(f"  Std of differences: {np.std(pixel_diff):.2f}")
        
        # 8. Analyze difference distribution
        diff_bins = [0, 1, 5, 10, 20, 50, 100, 255]
        print(f"\n📊 Difference distribution:")
        for i in range(len(diff_bins)-1):
            low = diff_bins[i]
            high = diff_bins[i+1]
            count = np.sum((pixel_diff > low) & (pixel_diff <= high))
            if count > 0:
                percentage = (count / total_pixels) * 100
                print(f"  {low:3d}-{high:3d}: {count:6d} pixels ({percentage:.2f}%)")
        
        # 9. Check for color shift warning
        if result['ssim_score'] > 0.9 and result['percent_difference'] > 50:
            print("\n⚠️ SUSPICIOUS: High SSIM but high pixel difference")
            
            if result['histogram_similarity'] and result['histogram_similarity'] < 0.5:
                result['warning'] = f"Color shift detected: SSIM={result['ssim_score']:.4f}, " \
                                   f"Pixel Diff={result['percent_difference']:.2f}%, " \
                                   f"HistSim={result['histogram_similarity']:.4f}"
                print(f"  {result['warning']}")
                
                # Adjust SSIM based on histogram difference
                result['ssim_score'] = result['histogram_similarity']
                print(f"  Adjusted SSIM to {result['ssim_score']:.4f}")
                
            elif result['mean_brightness_diff'] > 50:
                result['warning'] = f"Brightness shift detected: Δ={result['mean_brightness_diff']:.1f}"
                print(f"  {result['warning']}")
                
                # Adjust SSIM
                brightness_factor = 1.0 - (result['mean_brightness_diff'] / 255.0)
                result['ssim_score'] = max(0.0, min(1.0, result['ssim_score'] * brightness_factor))
                print(f"  Adjusted SSIM to {result['ssim_score']:.4f}")
        
        # Create visualizations
        if output_dir:
            create_visualizations(prev_array, curr_gray, diff, result, previous_snapshot, current_snapshot, output_dir, pixel_diff)
        
        print("\n" + "="*70)
        print("🔬 IMAGE COMPARISON DEBUG - END")
        print("="*70 + "\n")
        
        return result
        
    except Exception as e:
        result['error'] = str(e)
        print(f"\n❌ CRITICAL ERROR: {e}")
        import traceback
        traceback.print_exc()
        print("="*70 + "\n")
        return result

def create_visualizations(prev_array, curr_gray, diff, result, previous_snapshot, current_snapshot, output_dir, pixel_diff=None):
    """Create visualization images"""
    try:
        os.makedirs(output_dir, exist_ok=True)
        
        # Prepare difference image for visualization
        diff_uint8 = (diff * 255).astype("uint8")
        
        # Use adaptive threshold based on image characteristics
        if result.get('mean_brightness_diff', 0) > 50:
            threshold_value = 60
        else:
            threshold_value = 30
            
        diff_mask = cv2.threshold(diff_uint8, threshold_value, 255, cv2.THRESH_BINARY)[1]
        
        # Create heatmap
        heatmap = cv2.applyColorMap(diff_uint8, cv2.COLORMAP_JET)
        heatmap_path = os.path.join(
            output_dir, 
            f"heatmap_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
        )
        cv2.imwrite(heatmap_path, heatmap)
        result['heatmap_image_path'] = heatmap_path
        print(f"  ✅ Heatmap saved: {os.path.basename(heatmap_path)}")
        
        # Create difference overlay
        diff_overlay = prev_array.copy()
        diff_overlay[diff_mask > 0] = [255, 0, 0]  # Red for changes
        
        diff_path = os.path.join(
            output_dir,
            f"diff_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
        )
        cv2.imwrite(diff_path, cv2.cvtColor(diff_overlay, cv2.COLOR_RGB2BGR))
        result['diff_image_path'] = diff_path
        print(f"  ✅ Diff image saved: {os.path.basename(diff_path)}")
        
        # If we have pixel difference data, create a difference intensity map
        if pixel_diff is not None:
            # Normalize pixel differences for visualization
            norm_diff = (pixel_diff / pixel_diff.max() * 255).astype(np.uint8) if pixel_diff.max() > 0 else pixel_diff
            intensity_map = cv2.applyColorMap(norm_diff, cv2.COLORMAP_HOT)
            intensity_path = os.path.join(
                output_dir,
                f"intensity_{previous_snapshot.id}_vs_{current_snapshot.id}.png"
            )
            cv2.imwrite(intensity_path, intensity_map)
            print(f"  ✅ Intensity map saved: {os.path.basename(intensity_path)}")
            
    except Exception as e:
        print(f"  ⚠️ Visualization creation failed: {e}")
