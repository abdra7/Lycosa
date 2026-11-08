# Lycosa artwork

These assets use the spider artwork supplied by the project owner.

- `lycosa-logo-source.png`: original transparent, rounded-square artwork (`123.png`).
- `lycosa-logo.png`: 512 px transparent logo for the repository README.
- `lycosa-avatar.png`: 512 px version with the supplied dark background for GitHub avatars.
- `lycosa-wordmark.jpg`: supplied artwork with the Lycosa name.

The desktop setup and sign-in screens use the same logo at
`desktop-tauri/public/lycosa_logo.png`. The app, installer, and taskbar icons live in
`desktop-tauri/src-tauri/icons/`.

The native icons use a tighter canvas than the presentation logo so they
match the visible size of other apps in the taskbar and Task Manager. The
artwork fills about 94% of the icon height, with transparent margins and its
original proportions preserved. The supplied source artwork stays unchanged.

To regenerate the desktop icons on Windows, run this from the repository root
(requires PowerShell 7 and the installed desktop npm dependencies):

```powershell
pwsh -File desktop-tauri/scripts/generate_icons.ps1
```

The script fits the visible source artwork onto a 1024 px canvas, uses Tauri
to produce the standard icon formats, and copies the seven desktop assets
into `desktop-tauri/src-tauri/icons/`. Intermediate files go into the ignored
`reports/branding/app-icons/` directory. README and sign-in presentation logos
retain their existing layout.
