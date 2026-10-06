# Security policy

## Supported versions

Only the newest version of OnTask gets security fixes. Until the first release,
that is the latest code on `main`; after it, the latest
[release](https://github.com/alejandrovegab/OnTask/releases). Older versions
are not patched.

## Reporting a vulnerability

Please report it privately, not in a public issue:
**[open a private report](https://github.com/alejandrovegab/OnTask/security/advisories/new)**
(the *Report a vulnerability* button on the repository's Security tab). Only
you and the maintainer can see it.

A useful report says:

- what an attacker could do, and what they would need first,
- the steps or a proof of concept that shows it,
- the OnTask commit or version, your macOS (or Windows/Linux) version, and
  whether you ran from source or `OnTask.app`.

You'll get a reply as soon as possible. Once a fix is ready it lands on `main`,
is noted in the [changelog](CHANGELOG.md), and the report is published as a
GitHub security advisory, crediting you unless you'd rather not be named.

## What counts

OnTask runs on your own computer, never touches the network, and reads only the
frontmost app and the active browser tab's address. Issues that matter most:

- input (an app's bundle ID, a page address, a settings file) that makes OnTask
  run commands or scripts it shouldn't,
- OnTask's settings or statistics becoming readable by other accounts, or
  leaking where they shouldn't (for example, a site name on the lock screen),
- a vulnerable dependency that OnTask actually uses.

Out of scope: anything that already requires control of your user account,
since that access could read OnTask's files directly.
