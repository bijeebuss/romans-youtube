import Foundation

/// One video in the merged, chronological feed (mirrors the server's JSON).
struct Video: Codable, Identifiable, Hashable {
    let id: String
    let title: String
    let channel: String
    let published: String       // ISO-8601 from YouTube's RSS
    let thumbnail: String

    var thumbnailURL: URL? { URL(string: thumbnail) }

    var publishedDate: Date? {
        ISO8601DateFormatter().date(from: published)
    }

    /// e.g. "3 days ago" — friendly relative date for the card.
    var relativeAge: String {
        guard let date = publishedDate else { return "" }
        let fmt = RelativeDateTimeFormatter()
        fmt.unitsStyle = .full
        return fmt.localizedString(for: date, relativeTo: Date())
    }
}

struct FeedResponse: Codable {
    let videos: [Video]
}

struct UserProfile: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let picture: String?

    var pictureURL: URL? {
        guard let picture, !picture.isEmpty else { return nil }
        return URL(string: picture)
    }
}

struct ProfilesResponse: Codable {
    let profiles: [UserProfile]
}

struct Channel: Codable, Identifiable, Hashable {
    let id: String
    let name: String
    let icon: String?

    var iconURL: URL? {
        guard let icon, !icon.isEmpty else { return nil }
        return URL(string: icon)
    }
}

struct ChannelsResponse: Codable {
    let channels: [Channel]
}

struct ChannelVideosResponse: Codable {
    let videos: [Video]
    let hasMore: Bool
    let nextOffset: Int
}

/// Response from /api/stream/<id> — a directly-playable URL for AVPlayer.
struct StreamResponse: Codable {
    let id: String
    let url: String
    let type: String?
}
