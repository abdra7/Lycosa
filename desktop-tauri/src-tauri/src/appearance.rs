//! Keep the native window frame aligned with the webview's chosen theme.

use serde::Deserialize;
use tauri::{Theme, WebviewWindow};

#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ThemeMode {
    Light,
    Dark,
}

#[tauri::command]
pub fn set_window_theme(window: WebviewWindow, mode: ThemeMode) -> Result<(), String> {
    // Also updates native controls and provides the Windows 10 fallback.
    window
        .set_theme(Some(match mode {
            ThemeMode::Light => Theme::Light,
            ThemeMode::Dark => Theme::Dark,
        }))
        .map_err(|error| error.to_string())?;

    #[cfg(windows)]
    {
        // Queue after Tauri's theme change so its default frame painting
        // cannot overwrite our explicit Windows 11 caption colors.
        let target = window.clone();
        window
            .run_on_main_thread(move || apply_caption_colors(&target, mode))
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

#[cfg(any(windows, test))]
fn palette(mode: ThemeMode) -> [u32; 3] {
    // RGB: --bg, --text, --border in styles/app.css, checked below.
    match mode {
        ThemeMode::Light => [0xffffff, 0x111827, 0xe5e7eb],
        ThemeMode::Dark => [0x0f1522, 0xe6eaf2, 0x283349],
    }
}

#[cfg(any(windows, test))]
fn colorref(rgb: u32) -> u32 {
    // Windows COLORREF stores bytes as 0x00BBGGRR, not CSS's 0xRRGGBB.
    ((rgb & 0xff) << 16) | (rgb & 0xff00) | ((rgb >> 16) & 0xff)
}

#[cfg(windows)]
fn apply_caption_colors(window: &WebviewWindow, mode: ThemeMode) {
    use windows_sys::Win32::Graphics::Dwm::{
        DwmSetWindowAttribute, DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR,
    };

    let hwnd = match window.hwnd() {
        Ok(hwnd) => hwnd,
        Err(error) => {
            log::warn!("Could not obtain the window frame: {error}");
            return;
        }
    };
    let attributes = [DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, DWMWA_BORDER_COLOR];
    for (attribute, rgb) in attributes.into_iter().zip(palette(mode)) {
        let color = colorref(rgb);
        // SAFETY: Tauri supplied a live HWND, this runs on its UI thread,
        // and the pointer and size describe a COLORREF valid for this call.
        let result = unsafe {
            DwmSetWindowAttribute(
                hwnd.0,
                attribute as u32,
                (&color as *const u32).cast(),
                std::mem::size_of::<u32>() as u32,
            )
        };
        // Custom caption colors require Windows 11 (build 22000+).
        // Older versions return E_INVALIDARG and retain Tauri's mode.
        if result < 0 && result as u32 != 0x80070057 {
            log::warn!("Could not apply frame attribute {attribute}: {result:#x}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn native_palette_matches_the_actual_app_theme_tokens() {
        let css = include_str!("../../src/styles/app.css");
        for (mode, selector) in [
            (ThemeMode::Light, ":root {"),
            (ThemeMode::Dark, ":root[data-theme=\"dark\"] {"),
        ] {
            let block = css
                .split_once(selector)
                .unwrap()
                .1
                .split('}')
                .next()
                .unwrap();
            for (name, expected) in ["--bg", "--text", "--border"]
                .into_iter()
                .zip(palette(mode))
            {
                let token = format!("{name}: #");
                let value = block
                    .split_once(&token)
                    .unwrap()
                    .1
                    .split(';')
                    .next()
                    .unwrap();
                assert_eq!(
                    u32::from_str_radix(value, 16).unwrap(),
                    expected,
                    "{mode:?} {name}"
                );
            }
        }
    }

    #[test]
    fn windows_colors_use_colorref_byte_order() {
        assert_eq!(colorref(0x0f1522), 0x22150f);
        assert_eq!(colorref(0xe6eaf2), 0xf2eae6);
    }

    #[test]
    fn only_explicit_supported_themes_cross_ipc() {
        assert!(matches!(
            serde_json::from_str::<ThemeMode>("\"dark\"").unwrap(),
            ThemeMode::Dark
        ));
        assert!(matches!(
            serde_json::from_str::<ThemeMode>("\"light\"").unwrap(),
            ThemeMode::Light
        ));
        assert!(serde_json::from_str::<ThemeMode>("\"system\"").is_err());
    }
}
