import SwiftUI

struct SubscriptionsView: View {
    @EnvironmentObject var config: AppConfig
    @StateObject private var model = SubscriptionsViewModel()

    let loadingVideo: Video?
    let onPlay: (Video) -> Void

    var body: some View {
        Group {
            if model.isLoading && model.channels.isEmpty {
                ProgressView("Loading…")
                    .padding(.top, 120)
            } else if let error = model.errorMessage, model.channels.isEmpty {
                VStack(spacing: 24) {
                    Image(systemName: "rectangle.stack")
                        .font(.system(size: 80))
                    Text(error).multilineTextAlignment(.center)
                    Button("Try Again") { Task { await model.load() } }
                }
                .padding(.top, 120)
            } else {
                List(model.channels) { channel in
                    NavigationLink {
                        ChannelVideosView(channel: channel, loadingVideo: loadingVideo, onPlay: onPlay)
                            .environmentObject(config)
                    } label: {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(channel.name)
                                .font(.headline)
                            Text(channel.id)
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.vertical, 8)
                    }
                }
            }
        }
        .onAppear {
            model.configure(with: config)
            Task { await model.load() }
        }
    }
}

struct ChannelVideosView: View {
    @EnvironmentObject var config: AppConfig
    @StateObject private var model = ChannelVideosViewModel()

    let channel: Channel
    let loadingVideo: Video?
    let onPlay: (Video) -> Void

    private let columns = [GridItem(.adaptive(minimum: 420, maximum: 520), spacing: 48)]

    var body: some View {
        ScrollView {
            if model.isLoading && model.videos.isEmpty {
                ProgressView("Loading…")
                    .padding(.top, 120)
            } else if let error = model.errorMessage, model.videos.isEmpty {
                VStack(spacing: 24) {
                    Image(systemName: "wifi.exclamationmark").font(.system(size: 80))
                    Text(error).multilineTextAlignment(.center)
                    Button("Try Again") { Task { await model.loadNextPage() } }
                }
                .padding(.top, 120)
            } else {
                LazyVGrid(columns: columns, spacing: 64) {
                    ForEach(Array(model.videos.enumerated()), id: \.element.id) { index, video in
                        VideoCardView(video: video, isLoading: loadingVideo == video)
                            .onTapGesture { onPlay(video) }
                            .onAppear {
                                if index == model.videos.count - 1 {
                                    Task { await model.loadNextPage() }
                                }
                            }
                    }

                    if model.isLoadingMore {
                        ProgressView()
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 40)
                    }
                }
                .padding(48)
            }
        }
        .navigationTitle(channel.name)
        .onAppear {
            model.configure(with: config, channel: channel)
            Task { await model.loadInitial() }
        }
    }
}
