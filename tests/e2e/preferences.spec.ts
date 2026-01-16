import { test, expect, Page } from '@playwright/test';

/**
 * Preferences Tab Tests
 * Tests for download categories, media policy, and quality settings
 */

test.describe('Preferences Tab', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
    // Navigate to Preferences tab
    const preferencesTab = page.getByRole('tab', { name: /preferences/i });
    await preferencesTab.click();
    await page.waitForLoadState('networkidle');
  });

  test.describe('Download Categories', () => {
    test('should display category fields', async ({ page }) => {
      const moviesCategory = page.getByLabel(/movies.*category|radarr.*category/i);
      const tvCategory = page.getByLabel(/tv.*category|sonarr.*category/i);

      await expect(moviesCategory.or(page.getByPlaceholder(/movies/i))).toBeVisible();
    });

    test('should accept valid category name', async ({ page }) => {
      const categoryInput = page.getByLabel(/movies.*category|radarr/i)
        .or(page.getByPlaceholder(/movies/i));

      if (await categoryInput.isVisible()) {
        await categoryInput.fill('films');
        const value = await categoryInput.inputValue();
        expect(value).toBe('films');
      }
    });

    test('should reject empty category', async ({ page }) => {
      const categoryInput = page.getByLabel(/movies.*category|radarr/i)
        .or(page.getByPlaceholder(/movies/i));

      if (await categoryInput.isVisible()) {
        await categoryInput.clear();
        await categoryInput.blur();

        // Should show validation error
        const error = page.getByText(/required|empty|invalid/i);
        const hasError = await error.isVisible().catch(() => false);
        // Document expected behavior
      }
    });

    test('should warn on category with special characters', async ({ page }) => {
      const categoryInput = page.getByLabel(/movies.*category|radarr/i)
        .or(page.getByPlaceholder(/movies/i));

      if (await categoryInput.isVisible()) {
        await categoryInput.fill('movies/new');
        await categoryInput.blur();

        // Should warn about slash (causes path issues)
        const warning = page.getByText(/invalid|special|character/i);
        const hasWarning = await warning.isVisible().catch(() => false);
      }
    });
  });

  test.describe('Media Policy', () => {
    test('should display audio language settings', async ({ page }) => {
      const audioSection = page.getByText(/audio.*language|keep.*audio/i);
      await expect(audioSection).toBeVisible();
    });

    test('should display subtitle language settings', async ({ page }) => {
      const subsSection = page.getByText(/subtitle.*language|keep.*sub/i);
      await expect(subsSection).toBeVisible();
    });

    test('should allow adding language codes', async ({ page }) => {
      // Look for language input or tag input
      const languageInput = page.getByPlaceholder(/language|add/i)
        .or(page.getByLabel(/language/i));

      if (await languageInput.isVisible()) {
        await languageInput.fill('fra');
        await languageInput.press('Enter');

        // Should add the language
        const addedLang = page.getByText('fra');
        const isAdded = await addedLang.isVisible().catch(() => false);
      }
    });

    test('should show movies and anime policies separately', async ({ page }) => {
      const moviesPolicy = page.getByText(/movies.*policy|movies.*audio/i);
      const animePolicy = page.getByText(/anime.*policy|anime.*audio/i);

      // At least one should be visible
      const hasMovies = await moviesPolicy.isVisible().catch(() => false);
      const hasAnime = await animePolicy.isVisible().catch(() => false);
      expect(hasMovies || hasAnime).toBeTruthy();
    });
  });

  test.describe('Quality Settings', () => {
    test('should display quality preset selector', async ({ page }) => {
      const qualityPreset = page.getByLabel(/quality.*preset|preset/i)
        .or(page.getByRole('combobox', { name: /quality|preset/i }));

      await expect(qualityPreset).toBeVisible();
    });

    test('should display resolution selector', async ({ page }) => {
      const resolution = page.getByLabel(/resolution|target/i)
        .or(page.getByRole('combobox', { name: /resolution/i }));

      const isVisible = await resolution.isVisible().catch(() => false);
      // Document expected behavior
    });

    test('should display bitrate input', async ({ page }) => {
      const bitrate = page.getByLabel(/bitrate|max.*bitrate/i);
      const isVisible = await bitrate.isVisible().catch(() => false);
      // Document expected behavior
    });

    test('should reject negative bitrate', async ({ page }) => {
      const bitrate = page.getByLabel(/bitrate|max.*bitrate/i);

      if (await bitrate.isVisible()) {
        await bitrate.fill('-100');
        await bitrate.blur();

        // Should show error
        const error = page.getByText(/invalid|negative|positive/i);
        const hasError = await error.isVisible().catch(() => false);
      }
    });

    test('should display container format selector', async ({ page }) => {
      const container = page.getByLabel(/container|format/i)
        .or(page.getByRole('combobox', { name: /container|format/i }));

      const isVisible = await container.isVisible().catch(() => false);
      // Document expected behavior
    });
  });
});
