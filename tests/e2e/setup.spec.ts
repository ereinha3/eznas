import { test, expect, Page } from '@playwright/test';

/**
 * Setup Tab Tests
 * Tests for path configuration, runtime settings, and validation
 */

test.describe('Setup Tab', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Navigate to Setup tab if not already there
    const setupTab = page.getByRole('tab', { name: /setup/i });
    if (await setupTab.isVisible()) {
      await setupTab.click();
    }
  });

  test.describe('Path Configuration', () => {
    test('should display path input fields', async ({ page }) => {
      await expect(page.getByLabel(/pool/i)).toBeVisible();
      await expect(page.getByLabel(/scratch/i)).toBeVisible();
      await expect(page.getByLabel(/appdata/i)).toBeVisible();
    });

    test('should show validation error for empty pool path', async ({ page }) => {
      const poolInput = page.getByLabel(/pool/i);
      await poolInput.clear();
      await poolInput.blur();

      // Look for validation error
      await expect(page.getByText(/required|empty|invalid/i)).toBeVisible({ timeout: 5000 });
    });

    test('should accept valid absolute path', async ({ page }) => {
      const poolInput = page.getByLabel(/pool/i);
      await poolInput.fill('/data/media/pool');
      await poolInput.blur();

      // Should not show error
      const errorVisible = await page.getByText(/invalid path/i).isVisible().catch(() => false);
      expect(errorVisible).toBeFalsy();
    });

    test('should handle path with spaces', async ({ page }) => {
      const poolInput = page.getByLabel(/pool/i);
      await poolInput.fill('/data/my media/pool');
      await poolInput.blur();

      // Should either accept or show specific warning
      const value = await poolInput.inputValue();
      expect(value).toContain('my media');
    });

    test('should handle path with special characters', async ({ page }) => {
      const poolInput = page.getByLabel(/pool/i);
      await poolInput.fill('/data/media (new)/pool');
      await poolInput.blur();

      // Should handle gracefully
      const value = await poolInput.inputValue();
      expect(value.length).toBeGreaterThan(0);
    });
  });

  test.describe('Runtime Settings', () => {
    test('should display UID/GID fields', async ({ page }) => {
      await expect(page.getByLabel(/user.*id|uid/i)).toBeVisible();
      await expect(page.getByLabel(/group.*id|gid/i)).toBeVisible();
    });

    test('should reject negative UID', async ({ page }) => {
      const uidInput = page.getByLabel(/user.*id|uid/i);
      await uidInput.fill('-1');
      await uidInput.blur();

      // Should show validation error
      await expect(page.getByText(/invalid|negative|positive/i)).toBeVisible({ timeout: 5000 });
    });

    test('should accept valid UID', async ({ page }) => {
      const uidInput = page.getByLabel(/user.*id|uid/i);
      await uidInput.fill('1000');
      await uidInput.blur();

      const value = await uidInput.inputValue();
      expect(value).toBe('1000');
    });

    test('should display timezone field', async ({ page }) => {
      await expect(page.getByLabel(/timezone/i)).toBeVisible();
    });

    test('should accept valid timezone', async ({ page }) => {
      const tzInput = page.getByLabel(/timezone/i);
      await tzInput.fill('America/New_York');

      const value = await tzInput.inputValue();
      expect(value).toBe('America/New_York');
    });
  });
});
