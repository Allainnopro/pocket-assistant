// No console window for the release build: the app lives in the tray.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    pocket_assistant_lib::run()
}
