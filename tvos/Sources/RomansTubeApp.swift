import SwiftUI

@main
struct RomansTubeApp: App {
    @StateObject private var config = AppConfig()

    var body: some Scene {
        WindowGroup {
            ContentView()
                .environmentObject(config)
        }
    }
}
