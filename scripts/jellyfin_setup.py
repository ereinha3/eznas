#!/usr/bin/env python3
"""Jellyfin post-deployment setup — configures plugins and theme.

Run after Jellyfin container is up and plugins are installed.
Idempotent — safe to re-run.

Usage:
    python3 scripts/jellyfin_setup.py              # dry-run
    python3 scripts/jellyfin_setup.py --execute     # apply
"""
import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration — edit these to match your stack
# ---------------------------------------------------------------------------

JELLYFIN_URL = "http://localhost:8096"
JELLYFIN_USER = "admin"
JELLYFIN_PASS = "adminadmin123"

JELLYSEERR_INTERNAL_URL = "http://jellyseerr:5055"
JELLYSEERR_EXTERNAL_URL = "http://localhost:5055"  # for iframe

SONARR_URL = "http://sonarr:8989"
RADARR_URL = "http://radarr:7878"
BAZARR_URL = "http://bazarr:6767"

# Theme CSS import
THEME_CSS = '@import url("https://cdn.jsdelivr.net/gh/lscambo13/ElegantFin@main/Theme/ElegantFin-jellyfin-theme-build-latest-minified.css");'

# ---------------------------------------------------------------------------
# Plugin config paths (relative to Jellyfin appdata)
# ---------------------------------------------------------------------------

APPDATA = Path(os.environ.get("JELLYFIN_APPDATA", "/home/ethan/eznas/app/jellyfin"))
PLUGIN_CONF = APPDATA / "data" / "plugins" / "configurations"
BRANDING_XML = APPDATA / "branding.xml"


def get_jellyseerr_api_key() -> str:
    """Fetch Jellyseerr API key by logging in via Jellyfin auth."""
    import urllib.request

    # Login
    data = json.dumps({"username": JELLYFIN_USER, "password": JELLYFIN_PASS}).encode()
    req = urllib.request.Request(
        f"{JELLYSEERR_INTERNAL_URL.replace('jellyseerr', 'localhost')}/api/v1/auth/jellyfin",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    try:
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        resp = opener.open(req)
        # Get settings with cookie
        req2 = urllib.request.Request(
            f"{JELLYSEERR_INTERNAL_URL.replace('jellyseerr', 'localhost')}/api/v1/settings/main"
        )
        resp2 = opener.open(req2)
        settings = json.loads(resp2.read())
        return settings.get("apiKey", "")
    except Exception as exc:
        print(f"  WARNING: Could not get Jellyseerr API key: {exc}")
        return ""


def get_service_api_key(service: str) -> str:
    """Read API key from generated secrets."""
    secrets_dir = Path("/home/ethan/eznas/nas_orchestrator/generated/.secrets")
    env_file = secrets_dir / f"{service}.env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "API_KEY=" in line:
                return line.split("=", 1)[1].strip()
    return ""


def update_xml_value(xml_path: Path, tag: str, value: str) -> bool:
    """Update a single XML element's text. Returns True if changed."""
    if not xml_path.exists():
        return False
    tree = ET.parse(xml_path)
    root = tree.getroot()
    elem = root.find(tag)
    if elem is None:
        return False
    if elem.text == value:
        return False
    elem.text = value
    tree.write(xml_path, encoding="utf-8", xml_declaration=True)
    return True


def configure_jellyfin_enhanced(dry_run: bool) -> None:
    """Configure Jellyfin Enhanced plugin."""
    config = PLUGIN_CONF / "Jellyfin.Plugin.JellyfinEnhanced.xml"
    if not config.exists():
        print("  Jellyfin Enhanced: not installed, skipping")
        return

    jellyseerr_key = get_jellyseerr_api_key()
    sonarr_key = get_service_api_key("sonarr")
    radarr_key = get_service_api_key("radarr")

    # Features to enable
    enable_features = [
        "QualityTagsEnabled",
        "LanguageTagsEnabled",
        "RatingTagsEnabled",
        "GenreTagsEnabled",
        "MetadataIconsEnabled",
        "ColoredRatingsEnabled",
        "PeopleTagsEnabled",
        "JellyseerrEnabled",
        "JellyseerrShowReportButton",
        "ArrLinksEnabled",
        "CalendarPageEnabled",
        "DownloadsPageEnabled",
    ]

    tree = ET.parse(config)
    root = tree.getroot()
    changed = False

    for feature in enable_features:
        elem = root.find(feature)
        if elem is not None and elem.text != "true":
            print(f"    Enable {feature}")
            if not dry_run:
                elem.text = "true"
            changed = True

    # Set Jellyseerr connection
    for tag, value in [
        ("JellyseerrUrls", JELLYSEERR_INTERNAL_URL),
        ("JellyseerrApiKey", jellyseerr_key),
    ]:
        elem = root.find(tag)
        if elem is not None and elem.text != value and value:
            print(f"    Set {tag}")
            if not dry_run:
                elem.text = value
            changed = True

    if changed and not dry_run:
        tree.write(config, encoding="utf-8", xml_declaration=True)
    print(f"  Jellyfin Enhanced: {'updated' if changed else 'already configured'}")


def configure_home_screen_sections(dry_run: bool) -> None:
    """Configure Home Screen Sections plugin with service connections."""
    config = PLUGIN_CONF / "Jellyfin.Plugin.HomeScreenSections.xml"
    if not config.exists():
        print("  Home Screen Sections: not installed, skipping")
        return

    jellyseerr_key = get_jellyseerr_api_key()
    sonarr_key = get_service_api_key("sonarr")
    radarr_key = get_service_api_key("radarr")

    content = config.read_text()
    changed = False

    replacements = [
        ("<JellyseerrUrl />", f"<JellyseerrUrl>{JELLYSEERR_INTERNAL_URL}</JellyseerrUrl>"),
        ("<JellyseerrExternalUrl />", f"<JellyseerrExternalUrl>{JELLYSEERR_EXTERNAL_URL}</JellyseerrExternalUrl>"),
        ("<JellyseerrApiKey />", f"<JellyseerrApiKey>{jellyseerr_key}</JellyseerrApiKey>"),
        ("<LazyLoadEnabled>false</LazyLoadEnabled>", "<LazyLoadEnabled>true</LazyLoadEnabled>"),
    ]

    # Sonarr/Radarr within their XML sections
    if sonarr_key and "<Sonarr>\n    <ApiKey />" in content:
        content = content.replace(
            "<Sonarr>\n    <ApiKey />\n    <Url />",
            f"<Sonarr>\n    <ApiKey>{sonarr_key}</ApiKey>\n    <Url>{SONARR_URL}</Url>",
        )
        changed = True
        print("    Connected Sonarr")

    if radarr_key and "<Radarr>\n    <ApiKey />" in content:
        content = content.replace(
            "<Radarr>\n    <ApiKey />\n    <Url />",
            f"<Radarr>\n    <ApiKey>{radarr_key}</ApiKey>\n    <Url>{RADARR_URL}</Url>",
        )
        changed = True
        print("    Connected Radarr")

    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            changed = True

    if changed and not dry_run:
        config.write_text(content)
    print(f"  Home Screen Sections: {'updated' if changed else 'already configured'}")


def configure_custom_tabs(dry_run: bool) -> None:
    """Set up the Requests tab with Jellyseerr iframe."""
    config = PLUGIN_CONF / "Jellyfin.Plugin.CustomTabs.xml"
    if not config.exists():
        print("  Custom Tabs: not installed, skipping")
        return

    content = config.read_text()
    if "<TabName>Requests</TabName>" in content:
        print("  Custom Tabs: Requests tab already configured")
        return

    new_content = f"""<?xml version="1.0" encoding="utf-8"?>
<PluginConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <Tabs>
    <CustomTab>
      <TabName>Requests</TabName>
      <TabContent><![CDATA[
        <style>
          #customTab_0 {{ padding: 0; margin: 0; height: calc(100vh - 3.5em); }}
          #customTab_0 iframe {{ width: 100%; height: 100%; border: none; }}
        </style>
        <iframe src="{JELLYSEERR_EXTERNAL_URL}"></iframe>
      ]]></TabContent>
      <TabIcon>request</TabIcon>
    </CustomTab>
  </Tabs>
</PluginConfiguration>"""

    print("    Adding Requests tab")
    if not dry_run:
        config.write_text(new_content)
    print("  Custom Tabs: configured")


def configure_theme(dry_run: bool) -> None:
    """Set the ElegantFin theme via branding.xml."""
    current = ""
    if BRANDING_XML.exists():
        current = BRANDING_XML.read_text()

    if THEME_CSS in current:
        print("  Theme: ElegantFin already set")
        return

    new_content = f"""<?xml version="1.0" encoding="utf-8"?>
<BrandingOptions xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <CustomCss>{THEME_CSS}</CustomCss>
  <SplashscreenEnabled>false</SplashscreenEnabled>
</BrandingOptions>"""

    print("    Setting ElegantFin theme")
    if not dry_run:
        BRANDING_XML.write_text(new_content)
    print("  Theme: ElegantFin configured")


def clear_skin_manager(dry_run: bool) -> None:
    """Clear Skin Manager selection to prevent conflicts with custom CSS."""
    config = PLUGIN_CONF / "Jellyfin.Plugin.SkinManager.xml"
    if not config.exists():
        return

    content = config.read_text()
    if "<selectedSkin />" in content:
        print("  Skin Manager: already cleared")
        return

    new_content = """<?xml version="1.0" encoding="utf-8"?>
<PluginConfiguration xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">
  <selectedSkin />
  <options />
</PluginConfiguration>"""

    print("    Clearing Skin Manager selection")
    if not dry_run:
        config.write_text(new_content)
    print("  Skin Manager: cleared")


def main():
    parser = argparse.ArgumentParser(description="Jellyfin post-deployment setup")
    parser.add_argument("--execute", action="store_true", help="Apply changes")
    args = parser.parse_args()

    if not args.execute:
        print("DRY RUN — pass --execute to apply\n")

    print("=== Jellyfin Plugin Configuration ===")
    configure_jellyfin_enhanced(dry_run=not args.execute)
    configure_home_screen_sections(dry_run=not args.execute)
    configure_custom_tabs(dry_run=not args.execute)
    configure_theme(dry_run=not args.execute)
    clear_skin_manager(dry_run=not args.execute)

    print()
    if args.execute:
        print("Done. Restart Jellyfin to apply: docker restart jellyfin")
    else:
        print("No changes made (dry run). Pass --execute to apply.")


if __name__ == "__main__":
    main()
