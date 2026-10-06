# Maintaining OnTask

Setup and recurring chores for whoever builds and releases OnTask. Nothing here
is needed to *use* the app.

## Local signing certificate (macOS)

`scripts/build-mac-app.sh` signs `OnTask.app` with a self-signed certificate
called **OnTask Local Signing**. macOS ties OnTask's permissions (Automation,
Accessibility, notifications) and its keychain item to the app's signature, so a
stable certificate is what keeps those grants across rebuilds. Without one the
script signs ad hoc, and macOS asks for everything again after each build.

The certificate only identifies your own builds to your own Mac. It is not
trusted anywhere else and cannot sign apps for other people; that needs an
Apple Developer ID.

### Creating it (once, about two minutes)

1. Open **Keychain Access** (Spotlight: "Keychain Access").
2. **Keychain Access → Certificate Assistant → Create a Certificate…**
   - Name: `OnTask Local Signing` (exactly; the build script looks for it)
   - Identity Type: **Self-Signed Root**
   - Certificate Type: **Code Signing**
   - Leave "Let me override defaults" unticked.
3. **Create**, then **Done**.
4. In the **login** keychain, double-click the certificate, expand **Trust**,
   set **Code Signing** to **Always Trust**, and close the window (your password
   saves it).
5. Check it: `security find-identity -v -p codesigning` should list
   `"OnTask Local Signing"` as a valid identity.

The first build that uses it shows *"codesign wants to sign using key …"*:
choose **Always Allow**, or it asks once per signed file.

To sign with a different certificate, set `ONTASK_SIGNING_IDENTITY` to its name.

### Renewing it

The default certificate lasts **one year**. When it expires the build falls
back to ad-hoc signing and says so. Create a new one with the same name,
delete the old one, rebuild, and grant OnTask's permissions once more.

## Dependencies

`requirements.txt` is a lock: every dependency of OnTask and its tooling at an
exact version, with the hashes of its files, for macOS, Windows and Linux at
once. Everything installs from it with `--require-hashes`.

- **Adding or changing a dependency:** edit `pyproject.toml`, then run
  `./scripts/update-lock.sh` and commit both files. CI fails if they disagree.
- **Taking newer versions:** `./scripts/update-lock.sh --upgrade` (everything)
  or `./scripts/update-lock.sh --upgrade-package NAME` (one), then run the tests.
- **A vulnerability report** (from CI's weekly `pip-audit`, or Dependabot):
  upgrade that package as above.

## Changelog and version

Add each user-visible change to the **Unreleased** section of
[CHANGELOG.md](../CHANGELOG.md) in the same pull request that makes it. At a
release, rename that section to the new version and date, start a fresh
**Unreleased** above it, and set the same version in `pyproject.toml` and
`src/ontask/__init__.py`.
