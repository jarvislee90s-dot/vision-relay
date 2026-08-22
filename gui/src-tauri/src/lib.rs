use tauri::Manager;

#[tauri::command]
fn ping() -> String { "pong".into() }

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![ping])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
