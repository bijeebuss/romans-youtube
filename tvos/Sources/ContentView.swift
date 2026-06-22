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
            TabView {
                Group {
                    if !config.isConfigured {
                        unconfiguredView
                    } else {
                        feedView
                    }
                }
                .tabItem {
                    Label("Home", systemImage: "play.rectangle")
                }

                SubscriptionsView(loadingVideo: loadingVideo, onPlay: play)
                    .environmentObject(config)
                    .tabItem {
                        Label("Subscriptions", systemImage: "rectangle.stack")
                    }
            }
            .navigationTitle("Roman-Tube")
            .toolbar {
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
        guard config.isConfigured else { return }
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
