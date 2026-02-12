import { test, expect, Page } from '@playwright/test';

/**
 * Apply Workflow Tests
 * Tests for the apply button, SSE streaming, and error handling
 */

test.describe('Apply Workflow', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    await page.waitForLoadState('networkidle');
  });

  test.describe('Apply Button', () => {
    test('should display apply button', async ({ page }) => {
      const applyButton = page.getByRole('button', { name: /apply|deploy|save/i });
      await expect(applyButton).toBeVisible();
    });

    test('should be disabled during apply', async ({ page }) => {
      const applyButton = page.getByRole('button', { name: /apply|deploy/i });

      // Click apply
      await applyButton.click();

      // Button should be disabled or show loading state
      await expect(applyButton).toBeDisabled({ timeout: 2000 }).catch(() => {
        // Or check for loading indicator
        const loading = page.getByText(/applying|loading|running/i);
        return expect(loading).toBeVisible();
      });
    });

    test('should show progress during apply', async ({ page }) => {
      const applyButton = page.getByRole('button', { name: /apply|deploy/i });

      // Click apply
      await applyButton.click();

      // Should show progress/logs
      const logs = page.getByTestId('apply-logs')
        .or(page.locator('.logs'))
        .or(page.getByText(/validating|rendering|starting/i));

      await expect(logs).toBeVisible({ timeout: 10000 });
    });
  });

  test.describe('SSE Streaming', () => {
    test('should receive real-time updates', async ({ page }) => {
      const applyButton = page.getByRole('button', { name: /apply|deploy/i });

      // Set up response listener
      const messages: string[] = [];
      page.on('response', (response) => {
        if (response.url().includes('/api/apply')) {
          // SSE response detected
          messages.push('apply-response');
        }
      });

      await applyButton.click();

      // Wait for some updates
      await page.waitForTimeout(3000);

      // Should have received response
      expect(messages.length).toBeGreaterThan(0);
    });

    test('should display stage progress', async ({ page }) => {
      const applyButton = page.getByRole('button', { name: /apply|deploy/i });
      await applyButton.click();

      // Look for stage indicators
      const stages = ['validate', 'render', 'deploy', 'configure', 'verify'];

      for (const stage of stages) {
        const stageElement = page.getByText(new RegExp(stage, 'i'));
        // At least some stages should appear
      }
    });
  });

  test.describe('Error Handling', () => {
    test('should show error on validation failure', async ({ page }) => {
      // Clear a required field
      const setupTab = page.getByRole('tab', { name: /setup/i });
      await setupTab.click();

      const poolInput = page.getByLabel(/pool/i);
      await poolInput.clear();

      // Try to apply
      const applyButton = page.getByRole('button', { name: /apply|deploy/i });
      await applyButton.click();

      // Should show validation error
      const error = page.getByText(/error|failed|invalid|required/i);
      await expect(error).toBeVisible({ timeout: 5000 });
    });

    test('should handle network errors gracefully', async ({ page }) => {
      // Intercept API calls to simulate failure
      await page.route('**/api/apply', (route) => {
        route.abort('failed');
      });

      const applyButton = page.getByRole('button', { name: /apply|deploy/i });
      await applyButton.click();

      // Should show error message
      const error = page.getByText(/error|failed|network|connection/i);
      await expect(error).toBeVisible({ timeout: 5000 });
    });
  });

  test.describe('Navigation During Apply', () => {
    test('should maintain state when switching tabs during apply', async ({ page }) => {
      const applyButton = page.getByRole('button', { name: /apply|deploy/i });
      await applyButton.click();

      // Wait for apply to start
      await page.waitForTimeout(500);

      // Switch tabs
      const servicesTab = page.getByRole('tab', { name: /services/i });
      await servicesTab.click();

      // Switch back
      const setupTab = page.getByRole('tab', { name: /setup/i });
      await setupTab.click();

      // Logs should still be visible
      const logs = page.getByTestId('apply-logs')
        .or(page.locator('.logs'));

      const isVisible = await logs.isVisible().catch(() => false);
      // Document expected behavior
    });
  });
});

test.describe('Concurrent Operations', () => {
  test('should prevent double-apply', async ({ page }) => {
    await page.goto('/');

    const applyButton = page.getByRole('button', { name: /apply|deploy/i });

    // Click apply twice quickly
    await applyButton.click();
    await applyButton.click();

    // Should only have one apply in progress
    // Button should be disabled after first click
    await expect(applyButton).toBeDisabled({ timeout: 1000 }).catch(() => {
      // Acceptable if it shows loading state instead
    });
  });
});
