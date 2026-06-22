import SwiftUI
import AVKit

/// Native tvOS player. Wraps AVPlayerViewController so we get the full Apple TV
/// transport UI (scrubbing, the swipe-down info panel, Siri Remote gestures).
struct PlayerView: UIViewControllerRepresentable {
    let url: URL

    func makeUIViewController(context: Context) -> AVPlayerViewController {
        let controller = AVPlayerViewController()
        let player = AVPlayer(url: url)
        controller.player = player
        controller.allowsPictureInPicturePlayback = false
        player.play()
        return controller
    }

    func updateUIViewController(_ controller: AVPlayerViewController, context: Context) {}
}
