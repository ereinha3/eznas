# NAS Orchestrator Test Suite

This directory contains automated tests for the NAS Orchestrator.

## Test Structure

```
tests/
├── conftest.py          # Shared fixtures
├── unit/                # Unit tests (fast, isolated)
│   ├── test_validators.py   # Config validation tests
│   ├── test_api.py          # API endpoint tests
│   └── test_pipeline.py     # Pipeline/remux tests
├── integration/         # Integration tests (with mocked services)
│   └── (future)
├── e2e/                 # End-to-end UI tests (Playwright)
│   ├── playwright.config.ts
│   ├── setup.spec.ts        # Setup tab tests
│   ├── services.spec.ts     # Services tab tests
│   ├── preferences.spec.ts  # Preferences tab tests
│   ├── apply.spec.ts        # Apply workflow tests
│   └── ui-edge-cases.spec.ts # Edge cases and security
└── fixtures/            # Test data files
```

## Running Tests

### Backend Tests (pytest)

```bash
# Activate virtual environment
source .venv/bin/activate

# Run all tests
pytest

# Run with verbose output
pytest -v

# Run specific test file
pytest tests/unit/test_validators.py

# Run specific test class
pytest tests/unit/test_validators.py::TestPathValidation

# Run specific test
pytest tests/unit/test_validators.py::TestPathValidation::test_valid_paths

# Run only unit tests
pytest -m unit

# Skip slow tests
pytest -m "not slow"

# Run with coverage
pytest --cov=orchestrator --cov-report=html
```

### E2E Tests (Playwright)

```bash
# Install Playwright
cd tests/e2e
npm install
npx playwright install

# Run all E2E tests
npm test

# Run with browser visible
npm run test:headed

# Run with Playwright UI
npm run test:ui

# Run specific browser
npm run test:chrome
npm run test:firefox
npm run test:safari
npm run test:mobile

# Debug tests
npm run test:debug

# View report
npm run report
```

### Before Running E2E Tests

E2E tests require the dev environment to be running:

```bash
# Option 1: Docker dev environment
./scripts/dev.sh up

# Option 2: Local development servers
# Terminal 1:
source .venv/bin/activate
uvicorn orchestrator.app:app --reload --port 8443

# Terminal 2:
cd frontend
npm run dev
```

## Test Categories

### Unit Tests
- **Validators**: Test configuration validation logic
- **API**: Test FastAPI endpoints with mocked dependencies
- **Pipeline**: Test FFmpeg command building and remux logic

### E2E Tests
- **Setup Tab**: Path configuration, runtime settings
- **Services Tab**: Service toggles, ports, credentials
- **Preferences Tab**: Categories, media policy, quality
- **Apply Workflow**: Apply button, SSE streaming, errors
- **Edge Cases**: XSS, input validation, keyboard navigation

## Writing New Tests

### Backend Test Template

```python
import pytest
from pathlib import Path

class TestMyFeature:
    """Tests for my feature."""

    def test_normal_case(self, sample_config):
        """Test normal behavior."""
        result = my_function(sample_config)
        assert result.success

    def test_edge_case(self, sample_config):
        """Test edge case handling."""
        sample_config["field"] = "edge_value"
        with pytest.raises(ValueError):
            my_function(sample_config)
```

### E2E Test Template

```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/');
  });

  test('should do something', async ({ page }) => {
    const element = page.getByRole('button', { name: /click me/i });
    await element.click();
    await expect(page.getByText(/success/i)).toBeVisible();
  });
});
```

## CI Integration

Tests can be run in CI with:

```yaml
# GitHub Actions example
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.12'

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Run unit tests
        run: pytest tests/unit -v

      - name: Install Playwright
        run: |
          cd tests/e2e
          npm ci
          npx playwright install --with-deps

      - name: Run E2E tests
        run: |
          cd tests/e2e
          npm test
```

## Coverage Goals

- Unit tests: >80% coverage of orchestrator/ code
- E2E tests: Cover all critical user workflows
- Focus on edge cases and error handling
