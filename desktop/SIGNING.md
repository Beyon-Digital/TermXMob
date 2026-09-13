# Signing Termx releases

This is the complete checklist for signed macOS and Windows releases built by
`.github/workflows/desktop.yml`. Nothing here is your Apple ID or password: signing
uses a **Developer ID Application** certificate and notarization uses an **App Store
Connect API key** (scoped and revocable).

If you would rather keep every Apple credential off GitHub, sign the published
artifacts locally instead — see [Local signing](#local-signing-no-github-secrets).

## What you need from Apple

1. **Apple Developer Program membership** (paid).
2. **Developer ID Application certificate** with its private key, exported as `.p12`:
   - Xcode → Settings → Accounts → your team → *Manage Certificates* → **+** →
     *Developer ID Application*, **or** developer.apple.com → *Certificates,
     Identifiers & Profiles* → *Certificates* → **+** → *Developer ID Application*
     (create a CSR in Keychain Access first).
   - Keychain Access → right-click the certificate → *Export* → save
     `DeveloperID.p12` with a password.
3. **App Store Connect API key**:
   - [appstoreconnect.apple.com](https://appstoreconnect.apple.com) → *Users and
     Access* → *Integrations* → *Team Keys* (App Store Connect API) → **Generate API
     Key**, role **Developer** or **Admin**.
   - Copy the **Key ID** (10 characters) and the **Issuer ID** (UUID).
   - Download the `.p8` private key **once** (Apple will not let you download it
     again; revoke and create a new one if lost).

You do **not** need an Apple ID, app-specific password, or two-factor codes for CI.

## Full setup with one command (recommended)

Once you have the `.cer` and the `.p8` on your Mac, `setup_github_signing.sh` does
everything: validates the certificate type, builds the `.p12`, imports it into your
keychain, writes `desktop/signing.env`, optionally signs/notarizes the installed app,
uploads all GitHub secrets, and can trigger the release:

```bash
desktop/scripts/setup_github_signing.sh \
  --cer ~/certs/developerid_application.cer \
  --key ~/certs/termx-developer-id.key \
  --api-key ~/Downloads/AuthKey_ABC123XYZ.p8 \
  --api-key-id ABC123XYZ \
  --api-issuer 00000000-0000-0000-0000-000000000000 \
  --sign-app /Applications/Termx.app \
  --tag v0.1.1
```

Use `--dry-run` first to validate without changing anything. The script refuses
certificates that are not **Developer ID Application** (for example Apple
Distribution) with a clear message.

## Push or update secrets only

The helper validates the inputs and uploads them with `gh secret set`:

```bash
# one-time, on your Mac (gh must be authenticated: gh auth login)
desktop/scripts/push_signing_secrets.sh \
  --p12 ~/certs/DeveloperID.p12 \
  --api-key ~/Downloads/AuthKey_ABC123XYZ.p8 \
  --api-key-id ABC123XYZ \
  --api-issuer 00000000-0000-0000-0000-000000000000
```

- It prompts for the `.p12` password (or pass `--p12-password`).
- The codesign identity is extracted from the certificate automatically; override with
  `--identity "Developer ID Application: Name (TEAMID)"`.
- Add `--windows-pfx ~/certs/windows.pfx` to also upload Windows signing secrets.
- Add `--dry-run` to preview; values are never printed or stored in files.
- Or place the values in `desktop/signing.env` (gitignored) and run it with no flags.

## Secrets reference

| Secret | Purpose |
| --- | --- |
| `APPLE_CERTIFICATE` | base64 of the Developer ID Application `.p12` |
| `APPLE_CERTIFICATE_PASSWORD` | `.p12` export password |
| `APPLE_SIGNING_IDENTITY` | e.g. `Developer ID Application: Name (TEAMID)` |
| `APPLE_API_KEY_ID` | App Store Connect API Key ID |
| `APPLE_API_ISSUER` | App Store Connect API Issuer ID |
| `APPLE_API_KEY_P8` | contents of `AuthKey_XXXX.p8` |
| `KEYCHAIN_PASSWORD` | random string for the temporary CI keychain (optional) |
| `WINDOWS_CERTIFICATE` | base64 of a Windows OV/EV code-signing `.pfx` (optional) |
| `WINDOWS_CERTIFICATE_PASSWORD` | `.pfx` password (optional) |

Manual equivalent if you prefer the GitHub UI:

```bash
base64 < DeveloperID.p12 | pbcopy      # APPLE_CERTIFICATE
cat AuthKey_ABC123XYZ.p8 | pbcopy      # APPLE_API_KEY_P8
```

## Build and verify

1. Re-run the workflow: **Actions → Desktop release → Run workflow** with
   `release_tag: vX.Y.Z` (or push a tag matching `tauri.conf.json`).
2. Verify locally after downloading the DMG:

   ```bash
   codesign --verify --deep --strict --verbose=2 /Volumes/Termx/Termx.app
   spctl --assess --type execute --verbose=2 /Volumes/Termx/Termx.app
   xcrun stapler validate Termx_X.Y.Z_aarch64.dmg
   ```

3. `gh secret list` shows which secrets exist (names only, never values).

## Rotate or revoke

- Revoke the API key from *Users and Access → Integrations* and push a replacement;
  no build changes required.
- Developer ID certificates can be revoked at developer.apple.com; export and push a
  new `.p12`.
- The Windows certificate is independent of Apple; buy/renew from any CA.

## Local signing (no GitHub secrets)

Keep the repository free of Apple credentials and sign the published artifacts on
your Mac:

```bash
# one-time: install the Developer ID certificate in your keychain, then store
# notarization credentials locally (API key or Apple ID prompt)
desktop/scripts/setup_macos_notary.sh \
  --key ~/Downloads/AuthKey_ABC123XYZ.p8 \
  --key-id ABC123XYZ \
  --issuer 00000000-0000-0000-0000-000000000000

# sign + notarize + staple the downloaded DMG (identity auto-detected)
desktop/scripts/sign_macos_release.sh --input ~/Downloads
```

The result is `signed-Termx_X.Y.Z_*.dmg`. Windows and Linux artifacts can be signed
the same way with `sign_windows_release.ps1` and `sign_linux_release.sh`.
