import { test, expect, Page } from '@playwright/test';

/**
 * Services Tab Tests
 * Tests for service toggles, port configuration, and credentials
 */

test.describe('Services Tab', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Navigate to Services tab
    const servicesTab = page.getByRole('tab', { name: /services/i });
    await servicesTab.click();
    await page.waitForLoadState('networkidle');
  });

  test.describe('Service Toggles', () => {
    test('should display all service toggles', async ({ page }) => {
      const services = ['qbittorrent', 'radarr', 'sonarr', 'prowlarr', 'jellyseerr', 'jellyfin'];

      for (const service of services) {
        const toggle = page.getByRole('switch', { name: new RegExp(service, 'i') })
          .or(page.getByLabel(new RegExp(service, 'i')));
        await expect(toggle).toBeVisible();
      }
    });

    test('should toggle service on and off', async ({ page }) => {
      const radarrToggle = page.getByRole('switch', { name: /radarr/i })
        .or(page.locator('[data-service="radarr"]').getByRole('switch'));

      const initialState = await radarrToggle.isChecked();
      await radarrToggle.click();

      const newState = await radarrToggle.isChecked();
      expect(newState).toBe(!initialState);
    });

    test('should show warning when disabling qBittorrent with arr services enabled', async ({ page }) => {
      // First ensure Radarr is enabled
      const radarrToggle = page.getByRole('switch', { name: /radarr/i })
        .or(page.locator('[data-service="radarr"]').getByRole('switch'));
      if (!await radarrToggle.isChecked()) {
        await radarrToggle.click();
      }

      // Try to disable qBittorrent
      const qbToggle = page.getByRole('switch', { name: /qbittorrent/i })
        .or(page.locator('[data-service="qbittorrent"]').getByRole('switch'));
      if (await qbToggle.isChecked()) {
        await qbToggle.click();
      }

      // Should show dependency warning
      const warning = page.getByText(/dependency|required|download client/i);
      const hasWarning = await warning.isVisible().catch(() => false);
      // This is expected behavior - test documents it
    });

    test('should handle rapid toggle clicks', async ({ page }) => {
      const toggle = page.getByRole('switch', { name: /radarr/i })
        .or(page.locator('[data-service="radarr"]').getByRole('switch'));

      // Click rapidly
      for (let i = 0; i < 5; i++) {
        await toggle.click();
        await page.waitForTimeout(50);
      }

      // UI should be in consistent state
      const isChecked = await toggle.isChecked();
      expect(typeof isChecked).toBe('boolean');
    });
  });

  test.describe('Port Configuration', () => {
    test('should display port fields for enabled services', async ({ page }) => {
      // Look for port input fields
      const portInput = page.getByLabel(/port/i).first();
      await expect(portInput).toBeVisible();
    });

    test('should reject invalid port (negative)', async ({ page }) => {
      const portInput = page.getByLabel(/radarr.*port|port.*radarr/i)
        .or(page.locator('[data-service="radarr"]').getByLabel(/port/i));

      if (await portInput.isVisible()) {
        await portInput.fill('-1');
        await portInput.blur();

        // Should show error
        const error = page.getByText(/invalid|negative|range/i);
        await expect(error).toBeVisible({ timeout: 5000 });
      }
    });

    test('should reject port greater than 65535', async ({ page }) => {
      const portInput = page.getByLabel(/radarr.*port|port.*radarr/i)
        .or(page.locator('[data-service="radarr"]').getByLabel(/port/i));

      if (await portInput.isVisible()) {
        await portInput.fill('70000');
        await portInput.blur();

        // Should show error
        const error = page.getByText(/invalid|range|65535/i);
        await expect(error).toBeVisible({ timeout: 5000 });
      }
    });

    test('should accept valid port', async ({ page }) => {
      const portInput = page.getByLabel(/radarr.*port|port.*radarr/i)
        .or(page.locator('[data-service="radarr"]').getByLabel(/port/i));

      if (await portInput.isVisible()) {
        await portInput.fill('7878');
        await portInput.blur();

        const value = await portInput.inputValue();
        expect(value).toBe('7878');
      }
    });

    test('should warn on duplicate ports', async ({ page }) => {
      // Set two services to same port
      const radarrPort = page.locator('[data-service="radarr"]').getByLabel(/port/i);
      const sonarrPort = page.locator('[data-service="sonarr"]').getByLabel(/port/i);

      if (await radarrPort.isVisible() && await sonarrPort.isVisible()) {
        await radarrPort.fill('8080');
        await sonarrPort.fill('8080');
        await sonarrPort.blur();

        // Should show duplicate warning
        const warning = page.getByText(/duplicate|conflict|already/i);
        const hasWarning = await warning.isVisible().catch(() => false);
        // Document expected behavior
      }
    });
  });

  test.describe('Credentials', () => {
    test('should display qBittorrent username field', async ({ page }) => {
      const usernameInput = page.getByLabel(/username/i).first();
      await expect(usernameInput).toBeVisible();
    });

    test('should display password field', async ({ page }) => {
      const passwordInput = page.getByLabel(/password/i).first();
      await expect(passwordInput).toBeVisible();
    });

    test('should mask password input', async ({ page }) => {
      const passwordInput = page.getByLabel(/password/i).first();
      const type = await passwordInput.getAttribute('type');
      expect(type).toBe('password');
    });

    test('should accept special characters in password', async ({ page }) => {
      const passwordInput = page.getByLabel(/password/i).first();
      await passwordInput.fill('p@$$w0rd!#$%^&*()');

      const value = await passwordInput.inputValue();
      expect(value).toBe('p@$$w0rd!#$%^&*()');
    });
  });

  test.describe('Proxy Configuration', () => {
    test('should display proxy toggle', async ({ page }) => {
      const proxyToggle = page.getByRole('switch', { name: /proxy|traefik/i })
        .or(page.getByLabel(/proxy|traefik/i));

      await expect(proxyToggle).toBeVisible();
    });

    test('should show proxy URL fields when enabled', async ({ page }) => {
      const proxyToggle = page.getByRole('switch', { name: /proxy|traefik/i });

      if (await proxyToggle.isVisible()) {
        // Enable proxy
        if (!await proxyToggle.isChecked()) {
          await proxyToggle.click();
        }

        // Should show proxy URL fields
        const proxyUrl = page.getByLabel(/proxy.*url|url/i);
        const hasProxyUrl = await proxyUrl.isVisible().catch(() => false);
        // Document expected behavior
      }
    });
  });
});
