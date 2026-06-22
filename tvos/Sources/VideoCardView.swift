import SwiftUI

/// A focusable thumbnail card. On tvOS the card lifts/scales when focused
/// (the standard `.card` button style) so it's obvious what's selected.
struct VideoCardView: View {
    let video: Video
    var isLoading: Bool = false

    private let thumbnailCornerRadius: CGFloat = 16
    private let titleHeight: CGFloat = 62
    private let metadataHeight: CGFloat = 26

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            ZStack {
                AsyncImage(url: video.thumbnailURL) { phase in
                    switch phase {
                    case .success(let image):
                        image
                            .resizable()
                            .scaledToFill()
                    case .failure:
                        Color.gray.opacity(0.3)
                            .overlay(Image(systemName: "photo"))
                    default:
                        Color.gray.opacity(0.2)
                            .overlay(ProgressView())
                    }
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .clipShape(RoundedRectangle(cornerRadius: thumbnailCornerRadius))

                if isLoading {
                    RoundedRectangle(cornerRadius: thumbnailCornerRadius).fill(.black.opacity(0.5))
                    ProgressView().scaleEffect(1.5).tint(.white)
                }
            }
            .aspectRatio(16 / 9, contentMode: .fit)

            Text(video.title)
                .font(.headline)
                .lineLimit(2)
                .truncationMode(.tail)
                .frame(height: titleHeight, alignment: .topLeading)

            HStack {
                Text(video.channel).font(.subheadline).foregroundStyle(.secondary)
                Spacer()
                Text(video.relativeAge).font(.caption).foregroundStyle(.tertiary)
            }
            .lineLimit(1)
            .frame(height: metadataHeight, alignment: .top)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .hoverEffect()          // tvOS focus lift
        .focusable()
    }
}
