import SwiftUI

struct AssistantBubbleView: View {
    @ObservedObject var controller: VoiceAssistantController

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(spacing: 12) {
                ZStack {
                    Circle()
                        .fill(
                            LinearGradient(
                                colors: [Color.cyan, Color.blue.opacity(0.85)],
                                startPoint: .topLeading,
                                endPoint: .bottomTrailing
                            )
                        )
                        .frame(width: 20, height: 20)

                    Circle()
                        .stroke(Color.white.opacity(0.7), lineWidth: 2)
                        .frame(width: 32, height: 32)
                        .scaleEffect(controller.state == .activeListening ? 1.1 : 0.92)
                        .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: controller.state)
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text(controller.titleText)
                        .font(.system(size: 24, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white)
                    Text(controller.subtitleText)
                        .font(.system(size: 13, weight: .medium, design: .rounded))
                        .foregroundStyle(.white.opacity(0.72))
                }

                Spacer()
            }

            Text(controller.liveTranscriptDisplay)
                .font(.system(size: 16, weight: .regular, design: .rounded))
                .foregroundStyle(.white.opacity(0.92))
                .lineLimit(3)
                .frame(maxWidth: .infinity, alignment: .leading)

            Capsule()
                .fill(Color.white.opacity(0.16))
                .frame(height: 6)
                .overlay(alignment: .leading) {
                    Capsule()
                        .fill(
                            LinearGradient(
                                colors: [Color.cyan.opacity(0.95), Color.green.opacity(0.9)],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(width: controller.levelBarWidth)
                }
        }
        .padding(24)
        .frame(width: AppConfig.bubbleSize.width, height: AppConfig.bubbleSize.height)
        .background(
            RoundedRectangle(cornerRadius: 30, style: .continuous)
                .fill(Color.black.opacity(0.72))
                .overlay(
                    RoundedRectangle(cornerRadius: 30, style: .continuous)
                        .stroke(Color.white.opacity(0.12), lineWidth: 1)
                )
        )
        .shadow(color: .black.opacity(0.35), radius: 30, y: 18)
    }
}
