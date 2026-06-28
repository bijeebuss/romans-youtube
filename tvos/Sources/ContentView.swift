import SwiftUI
import Combine

struct ContentView: View {
    @EnvironmentObject var config: AppConfig
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var model: FeedViewModelHolder = FeedViewModelHolder()

    @State private var showSettings = false
    @State private var selectedVideo: Video?
    @State private var loadingVideo: Video?
    @State private var playerURL: URL?
    @State private var playbackError: String?

    // Responsive grid: a few columns of wide thumbnails.
    private let columns = [GridItem(.adaptive(minimum: 420, maximum: 520), spacing: 48)]

    var body: some View {
        NavigationStack {
            Group {
                if !config.isConfigured {
                    unconfiguredView
                } else if !config.hasSelectedProfile {
                    ProfileSelectionView()
                        .environmentObject(config)
                } else {
                    mainTabs
                }
            }
            .navigationTitle("Roman-Tube")
            .toolbar {
                if config.hasSelectedProfile {
                    ToolbarItem(placement: .topBarLeading) {
                        Button {
                            config.selectedProfileID = nil
                        } label: {
                            Image(systemName: "person.crop.circle")
                        }
                    }
                }
                ToolbarItem(placement: .topBarTrailing) {
                    Button {
                        showSettings = true
                    } label: {
                        Image(systemName: "gearshape")
                    }
                }
            }
        }
        .onAppear {
            model.configure(with: config)
            refreshFeed()
        }
        .sheet(isPresented: $showSettings) {
            SettingsView()
                .environmentObject(config)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { refreshFeed() }
        }
        .onChange(of: showSettings) { _, isShowing in
            if !isShowing { refreshFeed() }
        }
        .onChange(of: config.selectedProfileID) { _, _ in
            model.vm?.clear()
            refreshFeed()
        }
        .onChange(of: playerURL) { _, url in
            if url == nil { refreshFeed() }
        }
        // Fullscreen native player once a stream URL is resolved.
        .fullScreenCover(item: Binding(
            get: { playerURL.map { PlayableItem(url: $0) } },
            set: { if $0 == nil { playerURL = nil } }
        )) { item in
            PlayerView(url: item.url)
                .ignoresSafeArea()
        }
        .alert("Couldn't play that video",
               isPresented: Binding(get: { playbackError != nil },
                                    set: { if !$0 { playbackError = nil } })) {
            Button("OK", role: .cancel) { }
        } message: {
            Text(playbackError ?? "")
        }
    }

    // MARK: - Feed

    private var mainTabs: some View {
        TabView {
            feedView
                .tabItem {
                    Label("Home", systemImage: "play.rectangle")
                }

            SubscriptionsView(loadingVideo: loadingVideo, onPlay: play)
                .environmentObject(config)
                .tabItem {
                    Label("Subscriptions", systemImage: "rectangle.stack")
                }
        }
    }

    private var feedView: some View {
        ScrollView {
            if let vm = model.vm {
                if vm.isLoading && vm.videos.isEmpty {
                    ProgressView("Loading…")
                        .padding(.top, 120)
                } else if let error = vm.errorMessage, vm.videos.isEmpty {
                    VStack(spacing: 24) {
                        Image(systemName: "wifi.exclamationmark").font(.system(size: 80))
                        Text(error).multilineTextAlignment(.center)
                        Button("Try Again") { Task { await vm.load(refresh: true) } }
                    }
                    .padding(.top, 120)
                } else {
                    LazyVGrid(columns: columns, spacing: 64) {
                        ForEach(vm.videos) { video in
                            VideoCardView(video: video,
                                          isLoading: loadingVideo == video)
                                .onTapGesture { play(video) }
                        }
                    }
                    .padding(48)
                }
            }
        }
        .refreshable { await model.vm?.load(refresh: true) }
    }

    private var unconfiguredView: some View {
        VStack(spacing: 32) {
            Image(systemName: "tv").font(.system(size: 100))
            Text("Welcome to Roman's Tube").font(.title)
            Text("Enter your home server address to get started.")
                .foregroundStyle(.secondary)
            Button("Open Settings") { showSettings = true }
                .buttonStyle(.borderedProminent)
        }
    }

    // MARK: - Playback

    private func play(_ video: Video) {
        guard let vm = model.vm else { return }
        loadingVideo = video
        Task {
            defer { loadingVideo = nil }
            do {
                let url = try await vm.streamURL(for: video)
                playerURL = url
            } catch {
                playbackError = "The server couldn't load this video. It may be private or age-restricted."
            }
        }
    }

    private func refreshFeed() {
        guard config.isConfigured, config.hasSelectedProfile else { return }
        Task {
            if model.vm == nil {
                model.configure(with: config)
            }
            await model.vm?.load(refresh: true)
        }
    }
}

/// Lets us create the @MainActor view-model after `config` is available.
@MainActor
final class FeedViewModelHolder: ObservableObject {
    @Published var vm: FeedViewModel?
    private var cancellable: AnyCancellable?

    func configure(with config: AppConfig) {
        guard vm == nil else { return }

        let vm = FeedViewModel(config: config)
        cancellable = vm.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
        self.vm = vm
    }
}

/// Wrapper so a resolved URL can drive `.fullScreenCover(item:)`.
struct PlayableItem: Identifiable {
    let url: URL
    var id: String { url.absoluteString }
}

private struct ProfileSelectionView: View {
    @EnvironmentObject var config: AppConfig

    @State private var profiles: [UserProfile] = []
    @State private var isLoading = false
    @State private var errorMessage: String?

    private let columns = [GridItem(.adaptive(minimum: 240, maximum: 320), spacing: 36)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 28) {
                Text("Choose a profile")
                    .font(.title2.weight(.semibold))

                if isLoading && profiles.isEmpty {
                    ProgressView("Loading profiles...")
                        .frame(maxWidth: .infinity)
                        .padding(.top, 80)
                } else if let errorMessage, profiles.isEmpty {
                    VStack(spacing: 24) {
                        Image(systemName: "person.crop.circle.badge.exclamationmark")
                            .font(.system(size: 80))
                        Text(errorMessage)
                            .multilineTextAlignment(.center)
                        Button("Try Again") { Task { await loadProfiles() } }
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 80)
                } else {
                    LazyVGrid(columns: columns, spacing: 36) {
                        ForEach(profiles) { profile in
                            Button {
                                config.selectedProfileID = profile.id
                            } label: {
                                VStack(spacing: 18) {
                                    ProfileAvatarView(profile: profile)
                                    Text(profile.name)
                                        .font(.headline)
                                        .lineLimit(2)
                                        .multilineTextAlignment(.center)
                                }
                                .frame(maxWidth: .infinity, minHeight: 210)
                                .padding(22)
                                .background(.thinMaterial)
                                .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }
            }
            .padding(48)
        }
        .task(id: config.serverAddress) {
            await loadProfiles()
        }
    }

    @MainActor
    private func loadProfiles() async {
        guard !isLoading else { return }
        guard let url = config.profilesURL() else {
            errorMessage = "Set the server address in Settings first."
            return
        }

        isLoading = true
        errorMessage = nil
        defer { isLoading = false }

        do {
            var request = URLRequest(url: url)
            request.timeoutInterval = 20
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse, http.statusCode == 200 else {
                errorMessage = "Server returned an unexpected response."
                return
            }
            profiles = try JSONDecoder().decode(ProfilesResponse.self, from: data).profiles
            if profiles.isEmpty {
                errorMessage = "No profiles yet. Create one on the server's /admin page."
            }
        } catch {
            errorMessage = "Couldn't reach the server.\n\(error.localizedDescription)"
        }
    }
}

private struct ProfileAvatarView: View {
    let profile: UserProfile

    private let size: CGFloat = 132

    var body: some View {
        ZStack {
            Circle()
                .fill(.secondary.opacity(0.18))

            AsyncImage(url: profile.pictureURL) { phase in
                if let image = phase.image {
                    image
                        .resizable()
                        .scaledToFill()
                } else {
                    Text(profile.name.prefix(1).uppercased())
                        .font(.system(size: 48, weight: .semibold))
                        .foregroundStyle(.secondary)
                }
            }
        }
        .frame(width: size, height: size)
        .clipShape(Circle())
        .overlay(
            Circle()
                .stroke(.white.opacity(0.18), lineWidth: 1)
        )
    }
}
