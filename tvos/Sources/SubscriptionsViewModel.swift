import Foundation

@MainActor
final class SubscriptionsViewModel: ObservableObject {
    @Published var channels: [Channel] = []
    @Published var isLoading = false
    @Published var errorMessage: String?

    private var config: AppConfig?
    private var profileID: String?

    func configure(with config: AppConfig) {
        self.config = config
        if profileID != config.selectedProfileID {
            profileID = config.selectedProfileID
            channels = []
            errorMessage = nil
        }
    }

    func load() async {
        guard !isLoading else { return }
        guard let url = config?.channelsURL() else {
            errorMessage = "Choose a profile first."
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 30
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                errorMessage = "Server returned an unexpected response."
                return
            }
            channels = try JSONDecoder().decode(ChannelsResponse.self, from: data).channels
            if channels.isEmpty {
                errorMessage = "No subscriptions yet. Add channels for this profile on the server's /admin page."
            }
        } catch {
            errorMessage = "Couldn't reach the server.\n\(error.localizedDescription)"
        }
    }
}

@MainActor
final class ChannelVideosViewModel: ObservableObject {
    @Published var videos: [Video] = []
    @Published var isLoading = false
    @Published var isLoadingMore = false
    @Published var errorMessage: String?

    private let pageSize = 24
    private var config: AppConfig?
    private var channel: Channel?
    private var profileID: String?
    private var nextOffset = 0
    private var hasMore = true

    func configure(with config: AppConfig, channel: Channel) {
        self.config = config
        if self.channel != channel || profileID != config.selectedProfileID {
            self.channel = channel
            profileID = config.selectedProfileID
            videos = []
            nextOffset = 0
            hasMore = true
            errorMessage = nil
        }
    }

    func loadInitial() async {
        guard videos.isEmpty else { return }
        await loadNextPage()
    }

    func loadNextPage() async {
        guard hasMore, !isLoading, !isLoadingMore else { return }
        guard let channel, let url = config?.channelVideosURL(channelID: channel.id, offset: nextOffset, limit: pageSize) else {
            errorMessage = "Set the server address in Settings first."
            return
        }

        let firstPage = videos.isEmpty
        if firstPage {
            isLoading = true
        } else {
            isLoadingMore = true
        }
        errorMessage = nil
        defer {
            isLoading = false
            isLoadingMore = false
        }

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 45
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                errorMessage = "Server returned an unexpected response."
                return
            }

            let decoded = try JSONDecoder().decode(ChannelVideosResponse.self, from: data)
            videos.append(contentsOf: decoded.videos)
            hasMore = decoded.hasMore
            nextOffset = decoded.nextOffset
        } catch {
            errorMessage = "Couldn't load videos.\n\(error.localizedDescription)"
        }
    }
}
