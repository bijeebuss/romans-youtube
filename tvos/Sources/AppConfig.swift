import Foundation

/// Holds the home-server address. Persisted in UserDefaults so it survives
/// app restarts. The parent sets this once on the Settings screen.
final class AppConfig: ObservableObject {
    @Published var serverAddress: String {
        didSet { UserDefaults.standard.set(serverAddress, forKey: Self.key) }
    }

    private static let key = "serverAddress"

    init() {
        serverAddress = UserDefaults.standard.string(forKey: Self.key) ?? ""
    }

    var isConfigured: Bool { baseURL != nil }

    /// Normalised base URL, e.g. "192.168.1.50:8000" → http://192.168.1.50:8000
    var baseURL: URL? {
        var raw = serverAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        if !raw.hasPrefix("http://") && !raw.hasPrefix("https://") {
            raw = "http://" + raw
        }
        return URL(string: raw)
    }

    func feedURL(refresh: Bool = false) -> URL? {
        guard let base = baseURL else { return nil }
        var url = base.appendingPathComponent("api/feed")
        if refresh, var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            comps.queryItems = [URLQueryItem(name: "refresh", value: "1")]
            url = comps.url ?? url
        }
        return url
    }

    func channelsURL() -> URL? {
        baseURL?.appendingPathComponent("api/channels")
    }

    func channelVideosURL(channelID: String, offset: Int, limit: Int) -> URL? {
        guard let base = baseURL else { return nil }
        let url = base
            .appendingPathComponent("api/channels")
            .appendingPathComponent(channelID)
            .appendingPathComponent("videos")
        guard var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return url }
        comps.queryItems = [
            URLQueryItem(name: "offset", value: String(offset)),
            URLQueryItem(name: "limit", value: String(limit)),
        ]
        return comps.url ?? url
    }

    func streamURL(for videoID: String) -> URL? {
        baseURL?.appendingPathComponent("api/stream").appendingPathComponent(videoID)
    }
}
