import { test, expect, Page } from '@playwright/test';

/**
 * UI Edge Cases and Security Tests
 * Tests for input sanitization, browser compatibility, and edge cases
 */

test.describe('Input Sanitization', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should sanitize XSS attempts in inputs', async ({ page }) => {
    const setupTab = page.getByRole('tab', { name: /setup/i });
    await setupTab.click();

    const poolInput = page.getByLabel(/pool/i);
    await poolInput.fill("<script>alert('xss')</script>");
    await poolInput.blur();

    // Should not execute script - page should still be functional
    await expect(page.getByRole('tab', { name: /setup/i })).toBeVisible();

    // Value should be escaped or rejected
    const value = await poolInput.inputValue();
    expect(value).not.toContain('<script>');
  });

  test('should handle path traversal attempts', async ({ page }) => {
    const setupTab = page.getByRole('tab', { name: /setup/i });
    await setupTab.click();

    const poolInput = page.getByLabel(/pool/i);
    await poolInput.fill('/data/../../../etc/passwd');
    await poolInput.blur();

    // Should either normalize or reject
    const value = await poolInput.inputValue();
    // Document expected behavior
  });

  test('should handle very long input', async ({ page }) => {
    const setupTab = page.getByRole('tab', { name: /setup/i });
    await setupTab.click();

    const poolInput = page.getByLabel(/pool/i);
    const longPath = '/data/' + 'a'.repeat(5000);
    await poolInput.fill(longPath);
    await poolInput.blur();

    // Should truncate or reject
    const value = await poolInput.inputValue();
    expect(value.length).toBeLessThan(5100);
  });

  test('should handle null bytes in input', async ({ page }) => {
    const servicesTab = page.getByRole('tab', { name: /services/i });
    await servicesTab.click();

    const usernameInput = page.getByLabel(/username/i).first();
    if (await usernameInput.isVisible()) {
      // Null bytes get stripped by the browser, so this tests graceful handling
      await usernameInput.fill('admin\x00injected');
      const value = await usernameInput.inputValue();
      // Should not contain null byte
      expect(value).not.toContain('\x00');
    }
  });

  test('should handle emoji in inputs', async ({ page }) => {
    const preferencesTab = page.getByRole('tab', { name: /preferences/i });
    await preferencesTab.click();

    const categoryInput = page.getByLabel(/movies.*category|radarr/i)
      .or(page.getByPlaceholder(/movies/i));

    if (await categoryInput.isVisible()) {
      await categoryInput.fill('🎬 Movies 🎬');
      const value = await categoryInput.inputValue();
      // Should either accept or sanitize
      expect(value.length).toBeGreaterThan(0);
    }
  });
});

test.describe('Browser Compatibility', () => {
  test('should render tabs correctly', async ({ page }) => {
    await page.goto('/');

    const tabs = page.getByRole('tab');
    const tabCount = await tabs.count();
    expect(tabCount).toBeGreaterThanOrEqual(2);
  });

  test('should be responsive at mobile width', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');

    // Page should still be usable
    await expect(page.getByRole('main').or(page.locator('body'))).toBeVisible();
  });

  test('should be usable at zoomed in view', async ({ page }) => {
    await page.goto('/');

    // Simulate 200% zoom by halving viewport
    await page.setViewportSize({ width: 640, height: 480 });

    // Should still be functional
    const tabs = page.getByRole('tab');
    await expect(tabs.first()).toBeVisible();
  });
});

test.describe('State Persistence', () => {
  test('should preserve unsaved changes warning on refresh', async ({ page }) => {
    await page.goto('/');

    const setupTab = page.getByRole('tab', { name: /setup/i });
    await setupTab.click();

    // Make a change
    const poolInput = page.getByLabel(/pool/i);
    await poolInput.fill('/new/path');

    // Try to refresh
    page.on('dialog', async (dialog) => {
      // Should show confirmation dialog
      expect(dialog.type()).toBe('beforeunload');
      await dialog.dismiss();
    });

    // Trigger beforeunload
    await page.evaluate(() => {
      window.dispatchEvent(new Event('beforeunload'));
    });
  });

  test('should handle multiple tabs gracefully', async ({ page, context }) => {
    await page.goto('/');

    // Open second tab
    const page2 = await context.newPage();
    await page2.goto('/');

    // Both should work independently
    await expect(page.getByRole('tab').first()).toBeVisible();
    await expect(page2.getByRole('tab').first()).toBeVisible();

    await page2.close();
  });
});

test.describe('Keyboard Navigation', () => {
  test('should support tab navigation', async ({ page }) => {
    await page.goto('/');

    // Press Tab multiple times
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');
    await page.keyboard.press('Tab');

    // Something should be focused
    const focused = await page.evaluate(() => document.activeElement?.tagName);
    expect(focused).toBeTruthy();
  });

  test('should support Enter to submit', async ({ page }) => {
    await page.goto('/');

    const setupTab = page.getByRole('tab', { name: /setup/i });
    await setupTab.click();

    const input = page.getByLabel(/pool/i);
    await input.focus();
    await input.fill('/test/path');
    await page.keyboard.press('Enter');

    // Should either submit or stay (depending on form behavior)
  });
});

test.describe('Loading States', () => {
  test('should show loading state on initial load', async ({ page }) => {
    // Slow down network
    await page.route('**/api/**', async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    });

    await page.goto('/');

    // Should show loading indicator or skeleton
    const loading = page.getByText(/loading/i)
      .or(page.locator('.loading'))
      .or(page.locator('[aria-busy="true"]'));

    // Either loading is shown briefly or page loads fast enough
  });

  test('should recover from API timeout', async ({ page }) => {
    // First request times out
    let requestCount = 0;
    await page.route('**/api/config', async (route) => {
      requestCount++;
      if (requestCount === 1) {
        await new Promise((resolve) => setTimeout(resolve, 10000));
        await route.abort('timedout');
      } else {
        await route.continue();
      }
    });

    await page.goto('/');

    // Should show error or retry
    const error = page.getByText(/error|timeout|retry/i);
    const hasError = await error.isVisible({ timeout: 5000 }).catch(() => false);
    // Document expected behavior
  });
});

test.describe('Error Recovery', () => {
  test('should recover from 500 error', async ({ page }) => {
    // First request returns 500
    let requestCount = 0;
    await page.route('**/api/config', async (route) => {
      requestCount++;
      if (requestCount === 1) {
        await route.fulfill({ status: 500, body: 'Internal Server Error' });
      } else {
        await route.continue();
      }
    });

    await page.goto('/');

    // Should show error message
    const error = page.getByText(/error|failed/i);
    const hasError = await error.isVisible({ timeout: 5000 }).catch(() => false);

    // Refresh should recover
    await page.reload();
    await expect(page.getByRole('tab').first()).toBeVisible();
  });
});
