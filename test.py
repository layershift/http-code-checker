import asyncio
import cv2
import numpy as np
import matplotlib.pyplot as plt

from skimage.metrics import structural_similarity as ssim
from playwright.async_api import async_playwright


# -----------------------------
# Screenshot Function
# -----------------------------
async def take_screenshot(url, output_path):
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-gpu"
            ]
        )

        page = await browser.new_page(
            viewport={"width": 1920, "height": 1080}
        )

        await page.goto(url, wait_until="networkidle")
        await page.screenshot(path=output_path, full_page=True)

        await browser.close()


# -----------------------------
# Image Comparison Function
# -----------------------------
def compare_images(img1_path, img2_path, diff_output="diff_heatmap.png"):
    img1 = cv2.imread(img1_path)
    img2 = cv2.imread(img2_path)

    if img1 is None or img2 is None:
        raise ValueError("Failed to load images")

    # Resize second image if needed
    if img1.shape != img2.shape:
        img2 = cv2.resize(img2, (img1.shape[1], img1.shape[0]))

    # Convert grayscale
    gray1 = cv2.cvtColor(img1, cv2.COLOR_BGR2GRAY)
    gray2 = cv2.cvtColor(img2, cv2.COLOR_BGR2GRAY)

    # Structural Similarity Index
    score, diff_map = ssim(gray1, gray2, full=True)

    diff_map = (diff_map * 255).astype("uint8")

    # Threshold difference noise
    _, binary_diff = cv2.threshold(diff_map, 25, 255, cv2.THRESH_BINARY)

    changed_pixels = np.count_nonzero(binary_diff)
    total_pixels = binary_diff.size

    percent_difference = (changed_pixels / total_pixels) * 100

    # ---------------- Heatmap Visualization ----------------
    plt.figure(figsize=(10, 6))
    plt.imshow(diff_map, cmap="hot")
    plt.title("Website Difference Heatmap")
    plt.axis("off")

    plt.savefig(diff_output, bbox_inches="tight", dpi=150)
    plt.close()

    return {
        "ssim_score": float(score),
        "percent_difference": round(percent_difference, 4),
        "changed_pixels": int(changed_pixels),
        "total_pixels": int(total_pixels),
        "heatmap_image": diff_output
    }


# -----------------------------
# Main Runner
# -----------------------------
async def main():
    url1 = "https://download.zoltan.man-1.vm.plesk-server.com"
    url2 = "https://download.zoltan.man-1.vm.plesk-server.com"

    screenshot1 = "screenshot1.png"
    screenshot2 = "screenshot2.png"

    print("Taking screenshot 1...")
    await take_screenshot(url1, screenshot1)
    input()
    print("Taking screenshot 2...")
    await take_screenshot(url2, screenshot2)

    print("Comparing images...")

    result = compare_images(
        screenshot1,
        screenshot2
    )

    print("\n===== Comparison Result =====")
    print(f"SSIM Score: {result['ssim_score']}")
    print(f"Difference Percentage: {result['percent_difference']}%")
    print(f"Changed Pixels: {result['changed_pixels']}")
    print(f"Total Pixels: {result['total_pixels']}")
    print(f"Heatmap Saved As: {result['heatmap_image']}")


# -----------------------------
# Entry Point
# -----------------------------
if __name__ == "__main__":
    asyncio.run(main())
