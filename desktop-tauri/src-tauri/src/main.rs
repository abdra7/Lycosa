// no console window behind the app in release builds on Windows
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    lycosa_desktop_lib::run()
}
