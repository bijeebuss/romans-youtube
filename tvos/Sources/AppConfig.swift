import Foundation

/// Holds the home-server address. Persisted in UserDefaults so it survives
/// app restarts. The parent sets this once on the Settings screen.
final class AppConfig: ObservableObject {
    @Published var serverAddress: String {
        didSet {
            UserDefaults.standard.set(serverAddress, forKey: Self.serverKey)
            if serverAddress != oldValue {
                selectedProfileID = nil
            }
        }
    }

    @Published var selectedProfileID: String? {
        didSet {
            UserDefaults.standard.set(selectedProfileID, forKey: Self.profileKey)
        }
    }

    private static let serverKey = "serverAddress"
    private static let profileKey = "selectedProfileID"

    init() {
        serverAddress = UserDefaults.standard.string(forKey: Self.serverKey) ?? ""
        selectedProfileID = UserDefaults.standard.string(forKey: Self.profileKey)
    }

    var isConfigured: Bool { baseURL != nil }
    var hasSelectedProfile: Bool { selectedProfileID?.isEmpty == false }

    /// Normalised base URL, e.g. "192.168.1.50:8000" → http://192.168.1.50:8000
    var baseURL: URL? {
        var raw = serverAddress.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !raw.isEmpty else { return nil }
        if !raw.hasPrefix("http://") && !raw.hasPrefix("https://") {
            raw = "http://" + raw
        }
        return URL(string: raw)
    }

    func profilesURL() -> URL? {
        baseURL?.appendingPathComponent("api/profiles")
    }

    func feedURL(refresh: Bool = false) -> URL? {
        guard let base = baseURL, let selectedProfileID else { return nil }
        var url = base
            .appendingPathComponent("api/profiles")
            .appendingPathComponent(selectedProfileID)
            .appendingPathComponent("feed")
        if refresh, var comps = URLComponents(url: url, resolvingAgainstBaseURL: false) {
            comps.queryItems = [URLQueryItem(name: "refresh", value: "1")]
            url = comps.url ?? url
        }
        return url
    }

    func channelsURL() -> URL? {
        guard let base = baseURL, let selectedProfileID else { return nil }
        return base
            .appendingPathComponent("api/profiles")
            .appendingPathComponent(selectedProfileID)
            .appendingPathComponent("channels")
    }

    func channelVideosURL(channelID: String, offset: Int, limit: Int) -> URL? {
        guard let base = baseURL, let selectedProfileID else { return nil }
        let url = base
            .appendingPathComponent("api/profiles")
            .appendingPathComponent(selectedProfileID)
            .appendingPathComponent("channels")
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
