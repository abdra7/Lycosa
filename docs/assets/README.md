# Lycosa artwork

These assets use the spider artwork supplied by the project owner.

- `lycosa-logo-source.png`: original transparent, rounded-square artwork (`123.png`).
- `lycosa-logo.png`: 512 px transparent logo for the repository README.
- `lycosa-avatar.png`: 512 px version with the supplied dark background for GitHub avatars.
- `lycosa-wordmark.jpg`: supplied artwork with the Lycosa name.

The desktop setup and sign-in screens use the same logo at
`desktop-tauri/public/lycosa_logo.png`. The app, installer, and taskbar icons live in
`desktop-tauri/src-tauri/icons/`.

To regenerate the desktop icons, run this from `desktop-tauri/`:

```sh
npm run tauri -- icon ../docs/assets/lycosa-logo-source.png --output ../reports/branding/icons
```

Copy `32x32.png`, `64x64.png`, `128x128.png`, `128x128@2x.png`, `icon.png`,
`icon.ico`, and `icon.icns` from that output into `src-tauri/icons/`. Copy
`icon.png` to both `public/lycosa_logo.png` and `../docs/assets/lycosa-logo.png`.
The generator's other platform outputs are not used by the desktop app.
