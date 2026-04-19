import CoreGraphics
import Foundation

enum AppConfig {
    static let wakeWord = "cactus"
    static let wakeWordAliases = [
        "cactus",
        "catcus",
        "caktus",
        "cactis",
        "cactuss",
        "captus",
        "kactus"
    ]
    static let silenceTimeout: TimeInterval = 1.15
    static let bubbleSize = CGSize(width: 430, height: 180)
    static let tabSize = CGSize(width: 280, height: 110)
    static let bubbleBottomMargin: CGFloat = 70
    static let bubbleTrailingMargin: CGFloat = 42
    static let tabTrailingMargin: CGFloat = 18
    static let tabVerticalOffset: CGFloat = 180
}
