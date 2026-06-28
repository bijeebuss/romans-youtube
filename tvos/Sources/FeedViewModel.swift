import Foundation

@MainActor
final class FeedViewModel: ObservableObject {
    @Published var videos: [Video] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private let config: AppConfig

    init(config: AppConfig) {
        self.config = config
    }

    func clear() {
        videos = []
        errorMessage = nil
    }

    func load(refresh: Bool = false) async {
        guard !isLoading else { return }
        guard let url = config.feedURL(refresh: refresh) else {
            errorMessage = "Choose a profile first."
            return
        }
        isLoading = true
        errorMessage = nil
        defer { isLoading = false }
        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 120
            print("Loading feed from \(url.absoluteString)")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else {
                errorMessage = "Server returned an unexpected response."
                print("Feed failed: non-HTTP response")
                return
            }
            guard http.statusCode == 200 else {
                let body = String(data: data, encoding: .utf8) ?? ""
                errorMessage = "Server returned HTTP \(http.statusCode) from /api/feed."
                print("Feed failed: HTTP \(http.statusCode) \(body)")
                return
            }
            let decoded = try JSONDecoder().decode(FeedResponse.self, from: data)
            videos = decoded.videos
            print("Loaded \(videos.count) feed videos")
            if videos.isEmpty {
                errorMessage = "No videos yet. Add channels for this profile on the server's /admin page."
            }
        } catch {
            errorMessage = "Couldn't reach the server.\n\(error.localizedDescription)"
            print("Feed request failed: \(error)")
        }
    }

    /// Resolve a playable stream URL for a video (server runs yt-dlp).
    func streamURL(for video: Video) async throws -> URL {
        guard let url = config.streamURL(for: video.id) else {
            throw URLError(.badURL)
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = 60   // yt-dlp extraction can take a few seconds
        print("Resolving stream from \(url.absoluteString)")
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
            let status = (response as? HTTPURLResponse)?.statusCode ?? -1
            let body = String(data: data, encoding: .utf8) ?? ""
            print("Stream request failed: HTTP \(status) \(body)")
            throw URLError(.cannotConnectToHost)
        }
        let decoded = try JSONDecoder().decode(StreamResponse.self, from: data)
        guard let playable = URL(string: decoded.url) else {
            throw URLError(.badServerResponse)
        }
        return playable
    }
}
