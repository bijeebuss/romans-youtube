import SwiftUI

struct SettingsView: View {
    @EnvironmentObject var config: AppConfig
    @Environment(\.dismiss) private var dismiss

    @State private var address: String = ""
    @State private var status: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("e.g. 192.168.1.50:8000", text: $address)
                        .keyboardType(.URL)
                        .textContentType(.URL)
                } header: {
                    Text("Home server address")
                } footer: {
                    Text("The IP address and port of your Raspberry Pi server on the home network. You can include http:// or leave it off.")
                }

                Section {
                    Button("Test Connection") { Task { await test() } }
                    if let status { Text(status).foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("Settings")
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") {
                        config.serverAddress = address
                        dismiss()
                    }
                }
            }
            .onAppear { address = config.serverAddress }
        }
    }

    private func test() async {
        status = "Checking…"
        // Temporarily resolve a base URL from the typed address.
        var raw = address.trimmingCharacters(in: .whitespacesAndNewlines)
        if !raw.hasPrefix("http") { raw = "http://" + raw }
        guard let base = URL(string: raw) else { status = "That doesn't look like a valid address."; return }
        let healthURL = base.appendingPathComponent("health")
        do {
            let (_, response) = try await URLSession.shared.data(from: healthURL)
            if let http = response as? HTTPURLResponse, http.statusCode == 200 {
                status = "✅ Connected!"
            } else {
                status = "Reached the address but got an unexpected response."
            }
        } catch {
            status = "❌ Couldn't connect. Check the IP, port, and that the server is running."
        }
    }
}
